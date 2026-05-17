"""Compare stuck pending jobs vs successful (done) jobs to find what triggers \n loops."""
import sys, os, io, re, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import sessionmaker
from Database.db_setup import engine, ProcessingJob, TriadBlock

Session = sessionmaker(bind=engine)
s = Session()

pending = s.query(ProcessingJob).filter(ProcessingJob.status == "pending").all()
# Get some successful triads for comparison
triads = s.query(TriadBlock).limit(20).all()

def analyze_prompt(text):
    """Return noise metrics for a prompt."""
    if not text:
        return {}
    chars = len(text)
    newlines = text.count('\n')
    tabs = text.count('\t')
    backslashes = len(re.findall(r'\\', text))
    latex_display = len(re.findall(r'\\\[|\\\]', text))
    latex_inline = len(re.findall(r'\\\(|\\\)', text))
    caret = len(re.findall(r'\^', text))
    code_blocks = len(re.findall(r'```', text))
    long_nums = len(re.findall(r'\b\d{15,}\b', text))
    # Math density: fraction of chars that are special math symbols
    math_chars = len(re.findall(r'[\\^{}_\[\]()]', text))
    math_density = math_chars / chars * 100 if chars else 0
    # Longest consecutive \n run
    longest_nl = max((len(m.group()) for m in re.finditer(r'\n+', text)), default=0)
    return {
        'chars': chars,
        'newlines': newlines,
        'tabs': tabs,
        'backslashes': backslashes,
        'latex_display': latex_display,
        'latex_inline': latex_inline,
        'carets': caret,
        'code_blocks': code_blocks,
        'long_nums': long_nums,
        'math_density': math_density,
        'longest_nl_run': longest_nl,
    }

print(f"{'='*80}")
print(f"  STUCK JOBS ANALYSIS — {len(pending)} pending, {len(triads)} done (comparison)")
print(f"{'='*80}")

# Analyze done jobs
done_stats = [analyze_prompt(t.raw_msg) for t in triads]
avg_done = {}
for key in done_stats[0]:
    avg_done[key] = sum(d[key] for d in done_stats) / len(done_stats)

print(f"\n  AVERAGE SUCCESSFUL JOB:")
for k, v in avg_done.items():
    print(f"    {k:20s}: {v:.1f}")

print(f"\n{'='*80}")
print(f"  STUCK PENDING JOBS (sorted by retry count):")
print(f"{'='*80}")

pending_sorted = sorted(pending, key=lambda j: j.retry_count, reverse=True)
for job in pending_sorted:
    stats = analyze_prompt(job.raw_prompt)
    # Flag anything significantly above done average
    flags = []
    if stats['math_density'] > avg_done['math_density'] * 1.5:
        flags.append(f"HIGH_MATH({stats['math_density']:.1f}% vs avg {avg_done['math_density']:.1f}%)")
    if stats['latex_display'] > avg_done['latex_display'] * 2:
        flags.append(f"LATEX_DISPLAY({stats['latex_display']} vs avg {avg_done['latex_display']:.0f})")
    if stats['latex_inline'] > avg_done['latex_inline'] * 2:
        flags.append(f"LATEX_INLINE({stats['latex_inline']} vs avg {avg_done['latex_inline']:.0f})")
    if stats['backslashes'] > avg_done['backslashes'] * 2:
        flags.append(f"BACKSLASHES({stats['backslashes']} vs avg {avg_done['backslashes']:.0f})")
    if stats['carets'] > avg_done['carets'] * 2:
        flags.append(f"CARETS({stats['carets']} vs avg {avg_done['carets']:.0f})")
    if stats['code_blocks'] > avg_done['code_blocks'] * 2:
        flags.append(f"CODE_BLOCKS({stats['code_blocks']} vs avg {avg_done['code_blocks']:.0f})")
    if stats['long_nums'] > 0:
        flags.append(f"LONG_NUMBERS({stats['long_nums']})")

    turn_ids = json.loads(job.raw_next_prompt) if job.raw_next_prompt else []
    print(f"\n  Job {job.id} | retries={job.retry_count} | turns={turn_ids[:3]}... | {stats['chars']:,} chars")
    print(f"    newlines={stats['newlines']} tabs={stats['tabs']} backslash={stats['backslashes']} latex_d={stats['latex_display']} latex_i={stats['latex_inline']} carets={stats['carets']} math%={stats['math_density']:.1f}")
    if flags:
        print(f"    FLAGS: {', '.join(flags)}")
    else:
        print(f"    FLAGS: (none — similar to successful jobs)")
    
    # Show first 300 chars of prompt content (after timestamp line)
    lines = (job.raw_prompt or "").split('\n')
    content_start = '\n'.join(lines[1:4])[:300]
    print(f"    Preview: {repr(content_start[:200])}")

# Summary
print(f"\n{'='*80}")
print(f"  COMPARISON SUMMARY")
print(f"{'='*80}")
print(f"  {'Metric':20s} | {'Avg Done':>10s} | {'Avg Stuck':>10s} | {'Ratio':>6s}")
print(f"  {'-'*20}-+-{'-'*10}-+-{'-'*10}-+-{'-'*6}")
stuck_stats = [analyze_prompt(j.raw_prompt) for j in pending]
for key in done_stats[0]:
    avg_s = sum(d[key] for d in stuck_stats) / max(len(stuck_stats), 1)
    ratio = avg_s / avg_done[key] if avg_done[key] > 0 else 0
    marker = " <<<" if ratio > 1.5 else ""
    print(f"  {key:20s} | {avg_done[key]:>10.1f} | {avg_s:>10.1f} | {ratio:>5.1f}x{marker}")

s.close()
