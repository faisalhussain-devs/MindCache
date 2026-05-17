"""Show details of failing question types."""
import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

data = json.load(open('BEAM/eval_data_beam.json', 'r', encoding='utf-8'))

for i, tc in enumerate(data['test_cases']):
    qtype = tc['question_type']
    if qtype in ('abstention', 'preference_following'):
        q = tc['question']
        a = tc['answer'][:300]
        print(f"Q{i+1} [{qtype}]")
        print(f"  Question: {q}")
        print(f"  Answer: {a}")
        print(f"  Sessions: {tc.get('session_ids')}")
        print(f"  Turns: {tc.get('turn_ids')}")
        print()
