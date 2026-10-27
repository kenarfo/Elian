# =====================================================================
#  ELIAN - Self-Improvement Menu
#  Pick a number. You never have to type a command.
#  Plain ASCII only on purpose, so it cannot break in the old console.
# =====================================================================

$ErrorActionPreference = 'Continue'
$env:PYTHONIOENCODING = 'utf-8'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$Root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location -LiteralPath $Root

# ---- find Python ----------------------------------------------------
$script:PyCmd = $null
$script:PyArgs = @()
if (Get-Command python -ErrorAction SilentlyContinue) {
    $script:PyCmd = 'python'
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $script:PyCmd = 'py'; $script:PyArgs = @('-3')
} else {
    Write-Host ''
    Write-Host '  Python was not found on this computer.' -ForegroundColor Red
    Write-Host '  Install Python, then run this file again.' -ForegroundColor Red
    exit 1
}

# Set once input runs out (window closed / piped input finished) so the menu
# can leave instead of redrawing itself for ever.
$script:Eof = $false
$script:LastExit = 0

function Ask {
    param([string]$Prompt)
    $answer = Read-Host $Prompt
    if ($null -eq $answer) { $script:Eof = $true; return '' }
    return $answer
}

# Runs the CLI and lets its output reach the console untouched. The exit code
# is published in $script:LastExit rather than returned, so it never mixes
# with (or swallows) the command's own output.
function Invoke-Elian {
    param([string[]]$CliArgs)
    & $script:PyCmd @script:PyArgs @CliArgs
    $script:LastExit = $LASTEXITCODE
}

function Pause-Screen {
    if ($script:Eof) { return }
    Write-Host ''
    Ask '  Press Enter to go back to the menu' | Out-Null
}

# ---- data -----------------------------------------------------------
function Get-Proposals {
    $raw = & $script:PyCmd @script:PyArgs -m core.self_improve.cli --json list 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $raw) { return @() }
    try {
        $items = ($raw -join "`n") | ConvertFrom-Json
        if ($null -eq $items) { return @() }
        return @($items)
    } catch { return @() }
}

function Get-LearnedModules {
    $raw = & $script:PyCmd @script:PyArgs -m core.self_improve.cli --json status 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $raw) { return @() }
    try {
        $obj = ($raw -join "`n") | ConvertFrom-Json
        return @($obj.learned_modules)
    } catch { return @() }
}

# Returns the chosen proposal, or $null if the user cancelled or mistyped.
function Select-Proposal {
    param([string]$Prompt, [string[]]$Only = @())

    $all = Get-Proposals
    if ($all.Count -eq 0) {
        Write-Host ''
        Write-Host '  There are no proposals yet.' -ForegroundColor Yellow
        Write-Host '  Choose option 2 first, so Elian can find a gap.' -ForegroundColor Yellow
        return $null
    }

    $items = $all
    if ($Only.Count -gt 0) {
        $items = @($all | Where-Object { $Only -contains $_.status })
        if ($items.Count -eq 0) {
            Write-Host ''
            Write-Host '  Nothing is waiting for that step right now.' -ForegroundColor Yellow
            return $null
        }
    }

    Write-Host ''
    $i = 0
    foreach ($p in $items) {
        $i++
        $ok = 'not tested'
        if ($p.verification -and $p.verification.passed) { $ok = 'tested OK' }
        Write-Host ("  {0}) {1}" -f $i, $p.id)
        Write-Host ("       {0}  |  {1}  |  {2}" -f $p.gap_name, $p.status, $ok)
    }

    Write-Host ''
    $answer = Ask ("  {0} - number of the proposal (Enter = cancel)" -f $Prompt)
    if ([string]::IsNullOrWhiteSpace($answer)) { return $null }

    $n = 0
    if (-not [int]::TryParse($answer.Trim(), [ref]$n)) {
        Write-Host '  That is not a number. Nothing was changed.' -ForegroundColor Yellow
        return $null
    }
    if ($n -lt 1 -or $n -gt $items.Count) {
        Write-Host '  That number is not on the list. Nothing was changed.' -ForegroundColor Yellow
        return $null
    }
    return $items[$n - 1]
}

function Select-FromList {
    param([object[]]$Items, [string]$Prompt)
    Write-Host ''
    $i = 0
    foreach ($item in $Items) {
        $i++
        Write-Host ("  {0}) {1}" -f $i, $item)
    }
    Write-Host ''
    $answer = Ask ("  {0} - number (Enter = cancel)" -f $Prompt)
    if ([string]::IsNullOrWhiteSpace($answer)) { return $null }
    $n = 0
    if (-not [int]::TryParse($answer.Trim(), [ref]$n) -or $n -lt 1 -or $n -gt $Items.Count) {
        Write-Host '  That is not a number on the list. Nothing was changed.' -ForegroundColor Yellow
        return $null
    }
    return $Items[$n - 1]
}

# ---- menu -----------------------------------------------------------
$choice = 'x'
while ($true) {
    if ($script:Eof) { break }

    Clear-Host
    Write-Host ''
    Write-Host '  ================================================' -ForegroundColor Cyan
    Write-Host '    ELIAN - Self-Improvement Menu' -ForegroundColor Cyan
    Write-Host '  ================================================' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '   1) Where do things stand?'
    Write-Host '   2) What is Elian bad at?'
    Write-Host '   3) Let Elian analyse everything and write code'
    Write-Host '   4) List all proposals'
    Write-Host '   5) Read the code Elian wrote'
    Write-Host '   6) Test the code Elian wrote'
    Write-Host '   7) ACCEPT a change   (this adds it for real)'
    Write-Host '   8) Say NO to a change'
    Write-Host '   9) Undo - remove a tool Elian added'
    Write-Host '  10) Tell Elian that something went wrong'
    Write-Host '   0) Quit'
    Write-Host ''

    $choice = (Ask '  Type a number and press Enter').Trim()

    if ($script:Eof) { break }
    if ($choice -eq '0') {
        Write-Host ''
        Write-Host '  Goodbye.' -ForegroundColor Cyan
        break
    }

    switch ($choice) {
        '1' {
            Write-Host ''
            Invoke-Elian @('-m','core.self_improve.cli','status')
            Pause-Screen
        }
        '2' {
            Write-Host ''
            Invoke-Elian @('-m','core.self_improve.cli','gaps')
            Pause-Screen
        }
        '3' {
            Write-Host ''
            Write-Host '  Elian will look for a gap, write code, and test it.' -ForegroundColor Yellow
            Write-Host '  It always stops before changing anything real.' -ForegroundColor Yellow
            Write-Host ''
            $go = (Ask '  Start? (y/n)').Trim()
            if ($go -match '^(y|yes)$') {
                Invoke-Elian @('-m','core.self_improve.cli','demo')
            } else {
                Write-Host '  Nothing was started.' -ForegroundColor Yellow
            }
            Pause-Screen
        }
        '4' {
            Write-Host ''
            Invoke-Elian @('-m','core.self_improve.cli','list')
            Pause-Screen
        }
        '5' {
            $p = Select-Proposal -Prompt 'Read'
            if ($null -ne $p) {
                Write-Host ''
                Invoke-Elian @('-m','core.self_improve.cli','review', $p.id)
            }
            Pause-Screen
        }
        '6' {
            $p = Select-Proposal -Prompt 'Test'
            if ($null -ne $p) {
                Write-Host ''
                Invoke-Elian @('-m','core.self_improve.cli','verify', $p.id)
                Write-Host ''
                Write-Host '  OK means every check passed. FAIL means do not accept it.' -ForegroundColor Yellow
            }
            Pause-Screen
        }
        '7' {
            $p = Select-Proposal -Prompt 'Accept' -Only @('VERIFIED','FAILED_VERIFICATION','DRAFT')
            if ($null -ne $p) {
                Write-Host ''
                Write-Host '  This will really add the code to your project.' -ForegroundColor Yellow
                Write-Host ("  File to be added : core\self_improve\learned\{0}.py" -f $p.module_name)
                Write-Host ("  Tool name        : {0}" -f $p.tool_name)
                if ($p.status -ne 'VERIFIED') {
                    Write-Host ''
                    Write-Host '  WARNING: this proposal is not marked as tested and passing.' -ForegroundColor Red
                    Write-Host '  Run option 6 on it first if you have not already.' -ForegroundColor Red
                }
                Write-Host ''
                $sure = (Ask '  Type YES (all capitals) to continue').Trim()
                if ($sure -ceq 'YES') {
                    Invoke-Elian @('-m','core.self_improve.cli','approve', $p.id, '--yes', '--note', 'approved from menu')
                    if ($script:LastExit -eq 0) {
                        Invoke-Elian @('-m','core.self_improve.cli','adopt', $p.id, '--replace')
                    }
                } else {
                    Write-Host '  Nothing was changed.' -ForegroundColor Yellow
                }
            }
            Pause-Screen
        }
        '8' {
            $p = Select-Proposal -Prompt 'Reject' -Only @('APPROVED','VERIFIED','FAILED_VERIFICATION','DRAFT')
            if ($null -ne $p) {
                $why = (Ask '  Why not? (a few words)').Trim()
                Write-Host ''
                Invoke-Elian @('-m','core.self_improve.cli','reject', $p.id, '--reason', $why)
            }
            Pause-Screen
        }
        '9' {
            $modules = Get-LearnedModules
            if ($modules.Count -eq 0) {
                Write-Host ''
                Write-Host '  Elian has not added any tools yet.' -ForegroundColor Yellow
            } else {
                $target = Select-FromList -Items $modules -Prompt 'Which one should be removed?'
                if ($null -ne $target) {
                    Write-Host ''
                    Write-Host ("  This deletes core\self_improve\learned\{0}.py" -f $target) -ForegroundColor Yellow
                    $sure = (Ask '  Type YES (all capitals) to remove it').Trim()
                    if ($sure -ceq 'YES') {
                        Invoke-Elian @('-m','core.self_improve.cli','unlearn', $target)
                    } else {
                        Write-Host '  Nothing was removed.' -ForegroundColor Yellow
                    }
                }
            }
            Pause-Screen
        }
        '10' {
            Write-Host ''
            $domain = (Ask '  Which area was it? (for example: scheduling, web, smart_home)').Trim()
            $cats = @('NO_TOOL_MATCH','SUCCESS','TIMEOUT','TOOL_ERROR','UNKNOWN','USER_CORRECTION','USER_FRUSTRATION','VAGUE_ANSWER','WRONG_TOOL')
            Write-Host ''
            Write-Host '  What kind of problem was it?'
            for ($i = 0; $i -lt $cats.Count; $i++) {
                Write-Host ("   {0}) {1}" -f ($i + 1), $cats[$i])
            }
            $catAnswer = (Ask '  Type the number').Trim()
            $catIndex = 0
            if (-not [int]::TryParse($catAnswer, [ref]$catIndex) -or $catIndex -lt 1 -or $catIndex -gt $cats.Count) {
                Write-Host '  That is not a number on the list. Nothing was recorded.' -ForegroundColor Yellow
            } else {
                $summary = (Ask '  What happened? (one or two sentences)').Trim()
                $tool = (Ask '  Which tool was used? (Enter = none)').Trim()
                $feedback = (Ask '  What did the user say? (Enter = nothing)').Trim()
                Write-Host ''
                Invoke-Elian @('-m','core.self_improve.cli','log','--domain',$domain,
                               '--category',$cats[$catIndex - 1],'--summary',$summary,
                               '--tool',$tool,'--feedback',$feedback)
            }
            Pause-Screen
        }
        default {
            Write-Host ''
            Write-Host '  That is not one of the numbers. Try again.' -ForegroundColor Yellow
        }
    }
}