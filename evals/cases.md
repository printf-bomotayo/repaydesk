# The investigator's eval cases

60 payments from the seeded week (seed 42, 2,000 borrowers, as of 2026-10-08). Expected answers are the generator's truth. Written by `python -m repaydesk.evals select`; don't edit by hand.

| Case (line id) | Kind | Expected | Note |
|---|---|---|---|
| `bank_csv:NGT597654822170` | business_name | LN-2026-000854 ₦90,750.00 |  |
| `bank_csv:NGT604966655434` | business_name | LN-2026-002063 ₦44,500.00 |  |
| `bank_csv:NGT955120802300` | business_name | LN-2026-001042 ₦422,500.00 |  |
| `bank_mt940:CST3082642801` | business_name | LN-2026-001359 ₦90,750.00 |  |
| `bank_mt940:CST4063222677` | business_name | LN-2026-001215 ₦121,000.00 |  |
| `bank_mt940:CST9758245850` | business_name | LN-2026-000717 ₦605,000.00 |  |
| `branch_cash:RCP-ABK-00003` | cash_name | LN-2026-001167 ₦89,250.00 |  |
| `branch_cash:RCP-IKJ-00001` | cash_name | LN-2026-000524 ₦135,000.00 |  |
| `branch_cash:RCP-MSH-00004` | cash_name | LN-2026-000836 ₦138,600.00 |  |
| `branch_cash:RCP-ONT-00002` | cash_name | LN-2026-000388 ₦605,000.00 |  |
| `branch_cash:RCP-OSH-00001` | cash_name | LN-2026-000839 ₦968,000.00 |  |
| `branch_cash:RCP-OSH-00006` | cash_name | LN-2026-002059 ₦968,000.00 |  |
| `branch_cash:RCP-IBD-00002` | cash_ref_typo | LN-2026-001509 ₦363,000.00 |  |
| `branch_cash:RCP-IBD-00003` | cash_ref_typo | LN-2026-001898 ₦181,500.00 |  |
| `branch_cash:RCP-IBD-00005` | cash_ref_typo | LN-2026-001923 ₦77,700.00 |  |
| `branch_cash:RCP-MSH-00001` | cash_ref_typo | LN-2026-000387 ₦76,650.00 |  |
| `branch_cash:RCP-ONT-00004` | cash_ref_typo | LN-2026-002073 ₦121,000.00 |  |
| `bank_csv:NGT276187041916` | group_leader | LN-2026-000031 ₦19,360.00, LN-2026-000032 ₦44,770.00, LN-2026-000033 ₦23,000.00 |  |
| `bank_csv:NGT352304779313` | group_leader | LN-2026-000070 ₦30,250.00, LN-2026-000071 ₦45,980.00, LN-2026-000072 ₦36,300.00, LN-2026-000073 ₦14,520.00 |  |
| `bank_csv:NGT592749011369` | group_leader | LN-2026-000024 ₦25,410.00, LN-2026-000026 ₦15,000.00, LN-2026-000027 ₦50,820.00, LN-2026-000028 ₦17,000.00, LN-2026-000029 ₦52,030.00 |  |
| `bank_csv:NGT719386308154` | group_leader | LN-2026-000065 ₦18,150.00, LN-2026-000066 ₦52,030.00, LN-2026-000068 ₦31,460.00 |  |
| `bank_mt940:CST4127433350` | group_leader | LN-2026-000196 ₦13,310.00, LN-2026-000197 ₦12,100.00, LN-2026-000198 ₦22,000.00, LN-2026-000201 ₦33,880.00 |  |
| `bank_mt940:CST4333226400` | group_leader | LN-2026-000160 ₦36,300.00, LN-2026-000161 ₦38,720.00, LN-2026-000164 ₦38,720.00 |  |
| `bank_mt940:CST7636973606` | group_leader | LN-2026-000173 ₦36,300.00, LN-2026-000177 ₦29,040.00 |  |
| `bank_mt940:CST9610591552` | group_leader | LN-2026-000144 ₦24,200.00, LN-2026-000145 ₦13,500.00 |  |
| `bank_csv:NGT176130934284` | name_only | LN-2026-000924 ₦363,000.00 |  |
| `bank_csv:NGT775069929184` | name_only | LN-2026-000608 ₦181,500.00 |  |
| `bank_csv:NGT872389625578` | name_only | LN-2026-001424 ₦80,500.00 |  |
| `bank_mt940:CST5259726276` | name_only | LN-2026-001551 ₦90,750.00 |  |
| `bank_mt940:CST9086470232` | name_only | LN-2026-001599 ₦60,500.00 |  |
| `bank_mt940:CST9410314097` | name_only | LN-2026-001559 ₦363,000.00 |  |
| `bank_csv:NGT508445624821` | payer_only | LN-2026-000832 ₦60,500.00 |  |
| `bank_csv:NGT824081761531` | payer_only | LN-2026-000906 ₦605,000.00 |  |
| `bank_mt940:CST1003427357` | payer_only | LN-2026-001885 ₦53,550.00 |  |
| `bank_mt940:CST2884064381` | payer_only | LN-2026-001851 ₦150,000.00 |  |
| `bank_mt940:CST5665646753` | payer_only | LN-2026-001875 ₦90,750.00 |  |
| `bank_mt940:CST6160965451` | payer_only | LN-2026-001605 ₦968,000.00 |  |
| `bank_csv:NGT884445337785` | pidgin | LN-2026-001750 ₦242,000.00 |  |
| `bank_csv:NGT933598228053` | pidgin | LN-2026-001779 ₦375,500.00 |  |
| `bank_mt940:CST1741832283` | pidgin | LN-2026-001616 ₦22,000.00 |  |
| `pos:404545890043` | pos_blank | suspense | agent left the reference blank |
| `pos:590448177741` | pos_blank | suspense | agent left the reference blank |
| `bank_csv:NGT246718646875` | ref_typo | LN-2026-001940 ₦242,000.00 | reference typed as LN-2026-001904 |
| `bank_csv:NGT358971683761` | ref_typo | LN-2026-001978 ₦73,500.00 | reference typed as LN-2026-00197 |
| `bank_csv:NGT843337489368` | ref_typo | LN-2026-000063 ₦10,500.00 | reference typed as LN-2026-00063 |
| `bank_mt940:CST1067320913` | ref_typo | LN-2026-000863 ₦74,550.00 | reference typed as LN-2026-00063 |
| `bank_mt940:CST2041596850` | ref_typo | LN-2026-000238 ₦61,000.00 | reference typed as LN-2026-00238 |
| `bank_mt940:CST8127851667` | ref_typo | LN-2026-000697 ₦47,250.00 | reference typed as LN-2026-00097 |
| `bank_mt940:CST8515408090` | split_across_loans | LN-2026-000581 ₦605,000.00, LN-2026-000582 ₦131,250.00 |  |
| `bank_csv:NGT461930756223` | third_party | LN-2026-000543 ₦134,400.00 |  |
| `bank_csv:NGT605416234788` | third_party | LN-2026-001772 ₦139,650.00 |  |
| `bank_csv:NGT620299532700` | third_party | LN-2026-000206 ₦181,500.00 |  |
| `bank_csv:NGT734215115366` | third_party | LN-2026-000577 ₦100,800.00 |  |
| `bank_csv:NGT781947616066` | third_party | LN-2026-001751 ₦605,000.00 |  |
| `bank_csv:NGT813839034507` | third_party | LN-2026-001818 ₦60,500.00 |  |
| `bank_csv:NGT847383537085` | third_party | LN-2026-002113 ₦363,000.00 |  |
| `bank_mt940:CST3771084425` | third_party | LN-2026-000152 ₦56,870.00 |  |
| `bank_mt940:CST9452087280` | third_party | LN-2026-000039 ₦20,000.00 |  |
| `bank_mt940:CST9937503865` | third_party | LN-2026-001460 ₦968,000.00 |  |
| `bank_csv:NGT468631703776` | unresolvable | suspense | no name, reference or account a person could trace |
