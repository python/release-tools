<#
.Synopsis
    Uploads from a VSTS release build layout to python.org
.Description
    Given the downloaded/extracted build artifact from a release
    build run on python.visualstudio.com, this script uploads
    the files to the correct locations.
.Parameter build
    The location on disk of the extracted build artifact.
.Parameter user
    The username to use when logging into the host.
.Parameter server
    The host or PuTTY session name.
.Parameter target
    The subdirectory on the host to copy files to.
.Parameter tests
    The path to run download tests in.
.Parameter doc_htmlhelp
    Optional path besides -build to locate CHM files.
.Parameter embed
    Optional path besides -build to locate ZIP files.
#>
param(
    [Parameter(Mandatory=$true)][string]$build,
    [Parameter(Mandatory=$true)][string]$user,
    [string]$server="python-downloads",
    [string]$hostkey="",
    [string]$keyfile="",
    [string]$target="/srv/www.python.org/ftp/python",
    [string]$tests=${env:TEMP},
    [string]$doc_htmlhelp=$null,
    [string]$embed=$null
)

if (-not $build) { throw "-build option is required" }
if (-not $user) { throw "-user option is required" }

$tools = $script:MyInvocation.MyCommand.Path | Split-Path -parent;

if (-not ((Test-Path "$build\win32\python-*.exe") -or (Test-Path "$build\amd64\python-*.exe"))) {
    throw "-build argument does not look like a 'build' directory"
}

function find-putty-tool {
    param ([string]$n)
    $t = gcm $n -EA 0
    if (-not $t) { $t = gcm ".\$n" -EA 0 }
    if (-not $t) { $t = gcm "${env:ProgramFiles}\PuTTY\$n" -EA 0 }
    if (-not $t) { $t = gcm "${env:ProgramFiles(x86)}\PuTTY\$n" -EA 0 }
    if (-not $t) { throw "Unable to locate $n.exe. Please put it on $PATH" }
    return gi $t.Path
}

$p = gci -r "$build\python-*.exe" | `
    ?{ $_.Name -match '^python-(\d+\.\d+\.\d+)((a|b|rc)\d+)?-.+' } | `
    select -first 1 | `
    %{ $Matches[1], $Matches[2] }

"Uploading version $($p[0]) $($p[1])"
"  from: $build"
"    to: $($server):$target/$($p[0])"
""

# Upload files to the server
$pscp = find-putty-tool "pscp"
$plink = find-putty-tool "plink"

$pargs = "-batch"
if ($hostkey) {
    $pargs = "$pargs -hostkey $hostkey"
}
if ($keyfile) {
    $pargs = "$pargs -noagent -i $keyfile"
}

"Upload using $pscp and $plink"
""

if ($doc_htmlhelp) {
    $chm = gci -EA 0 $doc_htmlhelp\python*.chm, $doc_htmlhelp\python*.chm.asc
} else {
    $chm = gci -EA 0 $build\python*.chm, $build\python*.chm.asc
}

$d = "$target/$($p[0])/"
& $plink $pargs $user@$server mkdir $d
& $plink $pargs $user@$server chgrp downloads $d
& $plink $pargs $user@$server chmod o+rx $d
if ($chm) {
    & $pscp $pargs $chm.FullName "$user@${server}:$d"
    if (-not $?) { throw "Failed to upload $chm" }
}

$dirs = gci "$build" -Directory
if ($embed) {
    $dirs = ($dirs, (gi $embed)) | %{ $_ }
}

foreach ($a in $dirs) {
    "Uploading files from $($a.FullName)"
    pushd "$($a.FullName)"
    $exe = gci *.exe, *.exe.asc, *.zip, *.zip.asc
    $msi = gci *.msi, *.msi.asc, *.msu, *.msu.asc
    popd

    if ($exe) {
        & $pscp -batch $exe.FullName "$user@${server}:$d"
        if (-not $?) { throw "Failed to upload $exe" }
    }

    if ($msi) {
        $sd = "$d$($a.Name)$($p[1])/"
        & $plink $pargs $user@$server mkdir $sd
        & $plink $pargs $user@$server chgrp downloads $sd
        & $plink $pargs $user@$server chmod o+rx $sd
        & $pscp $pargs $msi.FullName "$user@${server}:$sd"
        if (-not $?) { throw "Failed to upload $msi" }
        & $plink $pargs $user@$server chgrp downloads $sd*
        & $plink $pargs $user@$server chmod g-x,o+r $sd*
    }
}

& $plink $pargs $user@$server chgrp downloads $d*
& $plink $pargs $user@$server chmod g-x,o+r $d*
& $pscp $pargs -ls "$user@${server}:$d"
