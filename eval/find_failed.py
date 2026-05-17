import json
with open('eval/results/eval_results.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
for q in data['questions']:
    if q['grade'] == 'fail':
        print(f"Q_ID: {q['question_id']}")
        print(f"Type: {q['question_type']}")
        print(f"Question: {q['question']}")
        print(f"Expected: {q['expected']}")
        print(f"Evidence IDs: {q['evidence_sessions']}")
        print('-'*50)
