# Thin forwarding wrapper; supplied Python arguments own validation and publication intent.
$ErrorActionPreference = "Stop"
& python -I "$PSScriptRoot/test_iran_reachability.py" @args
exit $LASTEXITCODE
