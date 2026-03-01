# MindCache Evaluation Report
**Date**: 2026-02-22

## Summary

| Metric | Value |
|--------|-------|
| Total Questions | 6 |
| Pass (incl. conflict) | 0 (0.0%) |
| Strict Pass | 0 (0.0%) |
| Fail | 6 |
| Conflict | 0 |

## Extraction Quality

| Evidence Captured | 51 |
|---|---|
| Evidence Missed | 0 |
| Capture Rate | 100.0% |

## Scores by Question Type

| Type | Total | Pass | Fail | Conflict | Rate |
|------|-------|------|------|----------|------|
| single-session-assistant | 6 | 0 | 6 | 0 | 0% |

## Per-Question Details

### c4f10528 [single-session-assistant]
**Q**: I'm planning to visit Bandung again and I was wondering if you could remind me of the name of that restaurant in Cihampelas Walk that serves a great Nasi Goreng?
**Expected**: Miss Bee Providore
**Grade**: FAIL
**Root**: ['ThaiCuisine', 'Recipes']
**Latency**: 8.93s

| # | Type | Content | Source Session | Source Type |
|---|------|---------|---------------|------------|
| 1 | user | Is looking for Thai dish recommendations beyond Pad Thai and Green Curry, having | ["8fd8d01a", "ultrachat_549617", "answer_sharegpt_IUWQYGQ_0", "b881fa89_1"] | injected |
| 2 | knowledge | Recommended Thai dishes include Som Tam (Papaya Salad), Larb (Meat Salad), Khao  | ["8fd8d01a", "ultrachat_549617", "answer_sharegpt_IUWQYGQ_0", "b881fa89_1"] | injected |

### 89527b6b [single-session-assistant]
**Q**: I'm going back to our previous conversation about the children's book on dinosaurs. Can you remind me what color was the scaly body of the Plesiosaur in the image?
**Expected**: The Plesiosaur had a blue scaly body.
**Grade**: FAIL
**Root**: []
**Latency**: 0.54s

*No memories retrieved.*

### e9327a54 [single-session-assistant]
**Q**: I'm planning to revisit Orlando. I was wondering if you could remind me of that unique dessert shop with the giant milkshakes we talked about last time?
**Expected**: The Sugar Factory at Icon Park.
**Grade**: FAIL
**Root**: ['Food']
**Latency**: 0.60s

*No memories retrieved.*

### 4c36ccef [single-session-assistant]
**Q**: Can you remind me of the name of the romantic Italian restaurant in Rome you recommended for dinner?
**Expected**: Roscioli
**Grade**: FAIL
**Root**: ['Entertaining', 'Travel', 'Food']
**Latency**: 0.37s

| # | Type | Content | Source Session | Source Type |
|---|------|---------|---------------|------------|
| 1 | user | I want recommendations for local restaurants or cafes in Iona. | ["da828711", "3c15770d_1", "d0a41222_1", "193c23bd_1", "ultrachat_260845", "answer_ultrachat_519486"] | injected |
| 2 | user | I want to know which Iona dining places have outdoor seating. | ["da828711", "3c15770d_1", "d0a41222_1", "193c23bd_1", "ultrachat_260845", "answer_ultrachat_519486"] | injected |
| 3 | user | I want to know which Iona dining places have the best selection of vegan options | ["da828711", "3c15770d_1", "d0a41222_1", "193c23bd_1", "ultrachat_260845", "answer_ultrachat_519486"] | injected |
| 4 | knowledge | Highly-rated dining establishments in Iona include The St. Columba Hotel Restaur | ["da828711", "3c15770d_1", "d0a41222_1", "193c23bd_1", "ultrachat_260845", "answer_ultrachat_519486"] | injected |
| 5 | knowledge | Dining establishments in Iona with outdoor seating options are Martyrs Bay Resta | ["da828711", "3c15770d_1", "d0a41222_1", "193c23bd_1", "ultrachat_260845", "answer_ultrachat_519486"] | injected |
| 6 | knowledge | Dining establishments in Iona with good vegan options are The Oran Mor Cafe & Cr | ["da828711", "3c15770d_1", "d0a41222_1", "193c23bd_1", "ultrachat_260845", "answer_ultrachat_519486"] | injected |
| 7 | user | Asks for the best way to serve Massaman curry with Som Tam and other dishes at a | ["8fd8d01a", "ultrachat_549617", "answer_sharegpt_IUWQYGQ_0", "b881fa89_1"] | injected |
| 8 | knowledge | Serving options for a Thai dinner party include: Family-Style Service (dishes in | ["8fd8d01a", "ultrachat_549617", "answer_sharegpt_IUWQYGQ_0", "b881fa89_1"] | injected |

### 6ae235be [single-session-assistant]
**Q**: I remember you told me about the refining processes at CITGO's three refineries earlier. Can you remind me what kind of processes are used at the Lake Charles Refinery?
**Expected**: Atmospheric distillation, fluid catalytic cracking (FCC), alkylation, and hydrotreating.
**Grade**: FAIL
**Root**: ['OilAndGas']
**Latency**: 0.56s

| # | Type | Content | Source Session | Source Type |
|---|------|---------|---------------|------------|
| 1 | knowledge | CITGO operates three refineries in the US. Lake Charles, LA: Atmospheric distill | ["8fd8d01a", "ultrachat_549617", "answer_sharegpt_IUWQYGQ_0", "b881fa89_1"] | injected |
| 2 | knowledge | Recommendations to increase refinery yield include: process optimization (operat | ["8fd8d01a", "ultrachat_549617", "answer_sharegpt_IUWQYGQ_0", "b881fa89_1"] | injected |
| 3 | knowledge | Ways to improve cat cracker yield include: optimization of operating conditions  | ["8fd8d01a", "ultrachat_549617", "answer_sharegpt_IUWQYGQ_0", "b881fa89_1"] | injected |

### 7e00a6cb [single-session-assistant]
**Q**: I'm planning my trip to Amsterdam again and I was wondering, what was the name of that hostel near the Red Light District that you recommended last time?
**Expected**: International Budget Hostel
**Grade**: FAIL
**Root**: ['Travel']
**Latency**: 0.61s

| # | Type | Content | Source Session | Source Type |
|---|------|---------|---------------|------------|
| 1 | user | I am planning a trip to Chicago for a conference next month. | ["da828711", "3c15770d_1", "d0a41222_1", "193c23bd_1", "ultrachat_260845", "answer_ultrachat_519486"] | injected |
| 2 | user | The conference is at McCormick Place. | ["da828711", "3c15770d_1", "d0a41222_1", "193c23bd_1", "ultrachat_260845", "answer_ultrachat_519486"] | injected |
| 3 | user | I need a hotel near McCormick Place with free Wi-Fi and a fitness center. | ["da828711", "3c15770d_1", "d0a41222_1", "193c23bd_1", "ultrachat_260845", "answer_ultrachat_519486"] | injected |
| 4 | user | My budget for the hotel is around $150 per night. | ["da828711", "3c15770d_1", "d0a41222_1", "193c23bd_1", "ultrachat_260845", "answer_ultrachat_519486"] | injected |
| 5 | user | I selected the Hyatt Regency McCormick Place. | ["da828711", "3c15770d_1", "d0a41222_1", "193c23bd_1", "ultrachat_260845", "answer_ultrachat_519486"] | injected |
| 6 | user | I want to know about the neighborhood around the Hyatt Regency McCormick Place ( | ["da828711", "3c15770d_1", "d0a41222_1", "193c23bd_1", "ultrachat_260845", "answer_ultrachat_519486"] | injected |
| 7 | knowledge | Recommended hotels near McCormick Place within budget ($130-$150) with free Wi-F | ["da828711", "3c15770d_1", "d0a41222_1", "193c23bd_1", "ultrachat_260845", "answer_ultrachat_519486"] | injected |
| 8 | knowledge | Hyatt Regency McCormick Place is connected to McCormick Place via an enclosed wa | ["da828711", "3c15770d_1", "d0a41222_1", "193c23bd_1", "ultrachat_260845", "answer_ultrachat_519486"] | injected |
| 9 | knowledge | The South Loop neighborhood around Hyatt Regency McCormick Place is considered s | ["da828711", "3c15770d_1", "d0a41222_1", "193c23bd_1", "ultrachat_260845", "answer_ultrachat_519486"] | injected |
| 10 | knowledge | Restaurants near Hyatt Regency McCormick Place include The Polo Cafe (24-hour di | ["da828711", "3c15770d_1", "d0a41222_1", "193c23bd_1", "ultrachat_260845", "answer_ultrachat_519486"] | injected |
| 11 | knowledge | Nearby attractions include Willis Tower and Millennium Park (20-minute walk). | ["da828711", "3c15770d_1", "d0a41222_1", "193c23bd_1", "ultrachat_260845", "answer_ultrachat_519486"] | injected |
| 12 | knowledge | The Mystic Falls Trailhead is located about 1.5 miles north of Old Faithful. | ["cb24e5d5_1", "db771e79_1", "sharegpt_UGg8d44_9", "1deb2c3b"] | injected |
| 13 | knowledge | To get to Mystic Falls Trailhead from Old Faithful, take Grand Loop Road north t | ["cb24e5d5_1", "db771e79_1", "sharegpt_UGg8d44_9", "1deb2c3b"] | injected |
| 14 | knowledge | To get to Mystic Falls Trailhead from Madison Junction, take Grand Loop Road sou | ["cb24e5d5_1", "db771e79_1", "sharegpt_UGg8d44_9", "1deb2c3b"] | injected |
| 15 | knowledge | Yellowstone National Park offers a free shuttle service during the summer season | ["cb24e5d5_1", "db771e79_1", "sharegpt_UGg8d44_9", "1deb2c3b"] | injected |
| 16 | knowledge | Commercial shuttle services like Xanterra Parks & Resorts and Yellowstone Tour & | ["cb24e5d5_1", "db771e79_1", "sharegpt_UGg8d44_9", "1deb2c3b"] | injected |
| 17 | knowledge | Yellowstone National Park offers free, ranger-led guided tours during the summer | ["cb24e5d5_1", "db771e79_1", "sharegpt_UGg8d44_9", "1deb2c3b"] | injected |
| 18 | knowledge | Commercial tour companies (e.g., Xanterra Parks & Resorts, Yellowstone Tour & Tr | ["cb24e5d5_1", "db771e79_1", "sharegpt_UGg8d44_9", "1deb2c3b"] | injected |
| 19 | knowledge | It is recommended to check the park's website for up-to-date trail information,  | ["cb24e5d5_1", "db771e79_1", "sharegpt_UGg8d44_9", "1deb2c3b"] | injected |
| 20 | knowledge | During peak season, the trailhead parking lot can fill up quickly, so arriving e | ["cb24e5d5_1", "db771e79_1", "sharegpt_UGg8d44_9", "1deb2c3b"] | injected |
| 21 | knowledge | It is essential to carry bear spray and know how to use it when in Yellowstone N | ["cb24e5d5_1", "db771e79_1", "sharegpt_UGg8d44_9", "1deb2c3b"] | injected |
