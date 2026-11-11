#requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$runner = $env:AIXAND_DEV_PYTHON
$prefixArgs = @()
if (-not $runner) {
    if (Get-Command python -ErrorAction SilentlyContinue) { $runner = 'python' }
    elseif (Get-Command py -ErrorAction SilentlyContinue) { $runner = 'py'; $prefixArgs = @('-3') }
    else {
        if ($args.Count -eq 0 -or $args[0] -in @('help', '--help', '-h')) {
            Write-Output 'Commands: help doctor develop lock check test build; suite artifact operations require the complete checkout'
            exit 0
        }
        Write-Error 'This developer helper requires Python 3.11+. Set AIXAND_DEV_PYTHON to its executable.'
        exit 2
    }
}
& $runner @prefixArgs -B (Join-Path $PSScriptRoot 'dev.py') @args
exit $LASTEXITCODE
