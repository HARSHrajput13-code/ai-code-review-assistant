Review the code in the SOURCE block ($line_count lines). Each source line starts with its line number and "|". The STATIC_FINDINGS block lists findings from deterministic tools.

<<<STATIC_FINDINGS_$nonce
$static_findings
STATIC_FINDINGS_$nonce>>>

<<<SOURCE_$nonce
$numbered_source
SOURCE_$nonce>>>

Respond with the JSON object.
