$ErrorActionPreference = 'Stop'

$model = if ($env:GEMINI_MODEL) { $env:GEMINI_MODEL } else { 'gemini-2.5-flash' }
$apiKey = $env:GEMINI_API_KEY
if (-not $apiKey) {
    throw 'GEMINI_API_KEY is not set.'
}

$contextPath = 'eval/results/beam_qa_context_questions.md'
$expectedPath = 'eval/results/beam_qa_expected_answers.md'
$answersPath = 'eval/results/beam_qa_independent_answers.md'
$reportPath = 'eval/results/beam_qa_independent_comparison_report.md'

function Invoke-GeminiText {
    param(
        [Parameter(Mandatory = $true)][string]$SystemPrompt,
        [Parameter(Mandatory = $true)][string]$UserPrompt,
        [int]$MaxOutputTokens = 65535
    )

    $uri = "https://generativelanguage.googleapis.com/v1beta/models/$model`:generateContent?key=$apiKey"
    $body = @{
        systemInstruction = @{
            parts = @(@{ text = $SystemPrompt })
        }
        contents = @(
            @{
                role = 'user'
                parts = @(@{ text = $UserPrompt })
            }
        )
        generationConfig = @{
            temperature = 0.1
            maxOutputTokens = $MaxOutputTokens
        }
    } | ConvertTo-Json -Depth 20

    $response = Invoke-RestMethod -Method Post -Uri $uri -ContentType 'application/json' -Body $body -TimeoutSec 900
    $parts = $response.candidates[0].content.parts
    return (($parts | ForEach-Object { $_.text }) -join "")
}

$answerSystemPrompt = @'
You are a strict context-grounded BEAM QA answerer.

CRITICAL RULES:
- You may use ONLY the context/questions markdown provided by the user.
- You must not use external knowledge.
- You must not infer unsupported facts.
- The expected answers are not provided. Do not mention or assume them.
- Answer all 20 questions in order.
- If the context lacks the requested fact, explicitly say: "Based on the provided context, there is no information about [requested fact]."
- For contradiction questions, actively search for both negative self-reports ("never", "no prior experience", "have not") and positive evidence (solved examples, practice pairs, verified work). If both exist, state the contradiction and ask which is correct.
- For counts, percentages, dates, number pairs, equations, and named examples, preserve the exact values from the context.
- If several similar facts exist, choose the one tied most directly to the question wording, and mention ambiguity only if needed.
- For fixed-count timeline questions, output exactly the requested number of items.
- For explanation/proof/preference questions, follow the requested style: use concrete numerical examples, step-by-step calculations, or algebraic proof when appropriate.

Output markdown only. Use this format exactly:

# BEAM QA Independent Answers

Source used for this pass: `eval/results/beam_qa_context_questions.md`

Expected answers were not consulted while drafting this file.

### 1. <question_id> [<type>]

<answer>

### 2. <question_id> [<type>]

<answer>

... continue through all 20 questions.
'@

$contextMarkdown = Get-Content -Path $contextPath -Raw -Encoding UTF8
$answerUserPrompt = @"
Answer the following BEAM QA context/questions markdown. Do not use expected answers.

$contextMarkdown
"@

Write-Host 'Generating independent answers from context/questions only...'
$answers = Invoke-GeminiText -SystemPrompt $answerSystemPrompt -UserPrompt $answerUserPrompt
Set-Content -Path $answersPath -Value $answers -Encoding UTF8
Write-Host "Wrote $answersPath"

$compareSystemPrompt = @'
You are a strict evaluator. Compare independent answers against expected answers only after the independent answer file has already been produced.

For each question:
- Mark PASS only if the independent answer is semantically correct and covers the expected answer/rubric.
- Mark FAIL if it is wrong, misses a crucial point, contradicts expected, or does not satisfy the rubric.
- Include whether context/retrieval appears to be the reason for the wrong answer. Use "Yes" only if the expected fact appears absent or materially contradicted in the context/questions file; otherwise use "No".
- Explain the wrong-answer reason briefly.

Return markdown with:
1. Overall grade as X / 20.
2. A per-question table with question id, grade, context reason, and reason.
3. Main failure patterns.
4. Prompt improvement recommendations.
'@

$expectedMarkdown = Get-Content -Path $expectedPath -Raw -Encoding UTF8
$answersMarkdown = Get-Content -Path $answersPath -Raw -Encoding UTF8
$compareUserPrompt = @"
Context/questions file used during answer phase:

$contextMarkdown

Independent answers:

$answersMarkdown

Expected answers and rubrics:

$expectedMarkdown
"@

Write-Host 'Comparing independent answers against expected answers...'
$report = Invoke-GeminiText -SystemPrompt $compareSystemPrompt -UserPrompt $compareUserPrompt
Set-Content -Path $reportPath -Value $report -Encoding UTF8
Write-Host "Wrote $reportPath"
