import hashlib

files = ['tests/golden/answer_golden_set.txt', 'tests/golden/route_eval_set.txt']
for f in files:
    h = hashlib.sha256(open(f, 'rb').read()).hexdigest()
    print(f'{f}: {h}')
