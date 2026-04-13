import json
from datetime import datetime
from Database.db_manager import DatabaseManager

TIME_GAP_MINUTES = 30  # Group messages within this time window

def parse_time(time_val):
    """Parse a timestamp from the dataset into a datetime object."""
    if not time_val:
        return None
    if isinstance(time_val, (int, float)):
        return datetime.fromtimestamp(time_val)
    if isinstance(time_val, str):
        try:
            return datetime.strptime(time_val, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return None
    return None

def group_by_time(conversation, gap_minutes=TIME_GAP_MINUTES):
    """
    Group consecutive turns within a conversation by time proximity.
    
    Simulates real live-scraping conditions:
    - While user is actively chatting (messages < 5 min apart), they stay in one group.
    - When there is a gap (user left, came back later), a new group starts.
    
    This mirrors how the browser extension works: it groups messages within
    an active session/tab, and resets when the session closes.
    """
    if not conversation:
        return []

    groups = [[conversation[0]]]

    for i in range(1, len(conversation)):
        prev_dt = parse_time(conversation[i - 1].get('time'))
        curr_dt = parse_time(conversation[i].get('time'))

        if prev_dt and curr_dt:
            diff_minutes = abs((curr_dt - prev_dt).total_seconds()) / 60
            if diff_minutes <= gap_minutes:
                groups[-1].append(conversation[i])
            else:
                groups.append([conversation[i]])
        else:
            # No timestamp available, keep in same group (safe default)
            groups[-1].append(conversation[i])

    return groups

def consolidate_group(group):
    """
    Merge a group of back-to-back turns into a single prompt+response+next_prompt.
    
    Format: <user> ... <llm> ... <user> ... <llm> ... 
    This gives the extraction LLM full conversational context so it can
    deduplicate facts and build coherent topic chains.
    """
    parts = []
    
    # Use the first turn's timestamp as the anchor for the group
    first_time = parse_time(group[0].get('time'))

    for idx, turn in enumerate(group):
        turn_time = parse_time(turn.get('time'))
        
        time_marker = ""
        if first_time and turn_time:
            diff_minutes = int(abs((turn_time - first_time).total_seconds()) / 60)
            time_marker = f"[+{diff_minutes}m] " if diff_minutes > 0 else "[Start: 0m] "

        prompt = turn.get('prompt', '')
        response = turn.get('response', '')
        
        if prompt:
            parts.append(f"{time_marker}<user> {prompt}")
        if response:
            parts.append(f"<llm> {response}")

    combined_text = " ".join(parts)
    return combined_text, first_time

BRANCH_MERGE_MINUTES = 5  # Merge conv arrays whose first messages are within this window

def queue_dataset(file_path):
    print(f"Loading '{file_path}' with {TIME_GAP_MINUTES}-minute time-based grouping...")
    
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    db = DatabaseManager()
    total_groups = 0
    total_turns = 0
    
    # Step 1: Parse and sort all conversations by their first message time
    parsed_convs = []
    for conv in data:
        if not conv:
            continue
        start_dt = parse_time(conv[0].get('time'))
        parsed_convs.append((start_dt, conv))
    parsed_convs.sort(key=lambda x: x[0] or datetime.min)

    # Step 2: Cluster conversation arrays whose start times are within BRANCH_MERGE_MINUTES.
    # This fixes ChatGPT's edited-prompt branches which start seconds/minutes apart
    # but are really the same conversation session.
    clusters = []
    for start_dt, conv in parsed_convs:
        merged = False
        for cluster in clusters:
            cluster_start = cluster['start_dt']
            if start_dt and cluster_start:
                diff = abs((start_dt - cluster_start).total_seconds()) / 60
                if diff <= BRANCH_MERGE_MINUTES:
                    cluster['turns'].extend(conv)
                    merged = True
                    break
        if not merged:
            clusters.append({'start_dt': start_dt, 'turns': list(conv)})

    # Step 3: For each cluster, dedup, sort, then apply 5-min sliding window
    for cluster in clusters:
        seen = set()
        deduped = []
        for turn in cluster['turns']:
            sig = (turn.get('time'), turn.get('prompt', ''), turn.get('response', ''))
            if sig not in seen:
                seen.add(sig)
                deduped.append(turn)
        
        deduped.sort(key=lambda x: x.get('time') or '')
        groups = group_by_time(deduped)
        
        for group in groups:
            combined_text, group_timestamp = consolidate_group(group)
            total_turns += len(group)
            
            if not combined_text.strip():
                continue

            db.add_to_queue(
                prompt=combined_text,
                response="",
                next_prompt="",
                timestamp=group_timestamp
            )
            total_groups += 1
            
    print(f"\nDone! Consolidated {total_turns} turns into {total_groups} grouped jobs.")
    print(f"Average group size: {total_turns/total_groups:.1f} turns per job")

if __name__ == '__main__':
    queue_dataset('finetuning_dataset.json')
