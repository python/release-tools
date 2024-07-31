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
.Parameter embed
    Optional path besides -build to locate ZIP files.
#>
param(
    [Parameter(Mandatory=$true)][string]$build,
    [Parameter(Mandatory=$true)][string]$user,
    [Parameter(Mandatory=$true)][string]$server,
    [Parameter(Mandatory=$true)][string]$hostkey,
    [Parameter(Mandatory=$true)][string]$keyfile,
    [string]$target="/srv/www.python.org/ftp/python",
    [string]$tests=${env:TEMP},
    [string]$embed=$null,
    [string]$sbom=$null
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

"Upload using $pscp and $plink"
""

$d = "$target/$($p[0])/"
& $plink -batch -hostkey $hostkey -noagent -i $keyfile "$user@$server" mkdir $d
& $plink -batch -hostkey $hostkey -noagent -i $keyfile "$user@$server" chgrp downloads $d
& $plink -batch -hostkey $hostkey -noagent -i $keyfile "$user@$server" chmod "a+rx" $d

$dirs = gci "$build" -Directory
if ($embed) {
    $dirs = ($dirs, (gi $embed)) | %{ $_ }
}
if ($sbom) {
    $dirs = ($dirs, $sbom) | %{ $_ }
}

foreach ($a in $dirs) {
    "Uploading files from $($a.FullName)"
    pushd "$($a.FullName)"
    $exe = gci *.exe, *.exe.asc, *.zip, *.zip.asc
    $msi = gci *.msi, *.msi.asc, *.msu, *.msu.asc
    $spdx_json = gci *.spdx.json
    popd

    if ($exe) {
        & $pscp -batch -hostkey $hostkey -noagent -i $keyfile $exe.FullName "$user@${server}:$d"
        if (-not $?) { throw "Failed to upload $exe" }
    }

    if ($spdx_json) {
        & $pscp -batch -hostkey $hostkey -noagent -i $keyfile $spdx_json.FullName "$user@${server}:$d"
        if (-not $?) { Write-Host "##[warning]Failed to upload $spdx_json" }
    }

    if ($msi) {
        $sd = "$d$($a.Name)$($p[1])/"
        & $plink -batch -hostkey $hostkey -noagent -i $keyfile "$user@$server" mkdir $sd
        & $plink -batch -hostkey $hostkey -noagent -i $keyfile "$user@$server" chgrp downloads $sd
        & $plink -batch -hostkey $hostkey -noagent -i $keyfile "$user@$server" chmod "a+rx" $sd
        & $pscp -batch -hostkey $hostkey -noagent -i $keyfile $msi.FullName "$user@${server}:$sd"
        if (-not $?) { throw "Failed to upload $msi" }
        & $plink -batch -hostkey $hostkey -noagent -i $keyfile "$user@$server" chgrp downloads $sd*
        & $plink -batch -hostkey $hostkey -noagent -i $keyfile "$user@$server" chmod "g-x,o+r" $sd*
    }
}

& $plink -batch -hostkey $hostkey -noagent -i $keyfile "$user@$server" chgrp downloads $d*
& $plink -batch -hostkey $hostkey -noagent -i $keyfile "$user@$server" chmod "g-x,o+r" $d*
& $pscp -batch -hostkey $hostkey -noagent -i $keyfile -ls "$user@${server}:$d"
