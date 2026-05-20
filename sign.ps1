#Requires -Version 5.1
<#
.SYNOPSIS
    Sign OneClickBackup executables with an Authenticode code signing certificate.

.DESCRIPTION
    This script signs the main application EXE and the installer EXE using
    signtool.exe and a code signing certificate. It supports three modes:

    1. Real certificate (recommended for distribution):
       Provide -CertThumbprint with the SHA-1 thumbprint of a certificate
       installed in your Windows certificate store.

    2. Self-signed certificate (for testing only):
       Run without -CertThumbprint and the script will look for an existing
       self-signed test cert or offer to create one.

    3. PFX file:
       Provide -PfxPath and -PfxPassword to sign with a .pfx file directly.

.PARAMETER ExePath
    Path to the main EXE to sign. Defaults to dist\OneClickBackup.exe.

.PARAMETER InstallerPath
    Path to the installer EXE to sign. Defaults to dist\OneClickBackup_Setup_1.2.0.exe.

.PARAMETER CertThumbprint
    SHA-1 thumbprint of the code signing certificate in the certificate store.
    Find yours with: Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert

.PARAMETER PfxPath
    Path to a .pfx certificate file (alternative to CertThumbprint).

.PARAMETER PfxPassword
    Password for the PFX file (used only with -PfxPath).

.PARAMETER TimestampServer
    URL of the RFC 3161 timestamp server. Timestamping ensures signatures
    remain valid after the certificate expires.

.EXAMPLE
    # Sign with a real certificate from the store
    .\sign.ps1 -CertThumbprint "A1B2C3D4E5F6..."

.EXAMPLE
    # Sign with a PFX file
    .\sign.ps1 -PfxPath ".\cert.pfx" -PfxPassword "secret"

.EXAMPLE
    # Create a self-signed test certificate and sign
    .\sign.ps1

.NOTES
    -----------------------------------------------------------------------
    HOW TO GET A REAL CODE SIGNING CERTIFICATE
    -----------------------------------------------------------------------
    For public distribution, you need a certificate from a trusted CA:

    1. Standard Code Signing Certificate (~$70-200/year):
       - DigiCert:    https://www.digicert.com/signing/code-signing-certificates
       - Sectigo:     https://sectigo.com/ssl-certificates-tls/code-signing
       - GlobalSign:  https://www.globalsign.com/en/code-signing-certificate

    2. EV Code Signing Certificate (~$300-600/year):
       - Stored on a hardware token (USB) for extra security
       - Provides immediate SmartScreen reputation (no warning period)
       - Required for kernel-mode drivers

    After purchasing:
       a. Complete the CA's identity verification process
       b. Install the certificate (follow CA instructions)
       c. Find the thumbprint:
            Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert | Format-List Subject, Thumbprint
       d. Run this script with -CertThumbprint <your-thumbprint>
    -----------------------------------------------------------------------
#>

[CmdletBinding()]
param(
    [Parameter()]
    [string]$ExePath = (Join-Path $PSScriptRoot "dist\OneClickBackup.exe"),

    [Parameter()]
    [string]$InstallerPath = (Join-Path $PSScriptRoot "dist\OneClickBackup_Setup_1.2.0.exe"),

    [Parameter()]
    [string]$CertThumbprint,

    [Parameter()]
    [string]$PfxPath,

    [Parameter()]
    [string]$PfxPassword,

    [Parameter()]
    [string]$TimestampServer = "http://timestamp.digicert.com"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Write-Status {
    param([string]$Message, [string]$Level = "INFO")
    switch ($Level) {
        "OK"    { Write-Host "  [OK] $Message" -ForegroundColor Green }
        "WARN"  { Write-Host "  [!]  $Message" -ForegroundColor Yellow }
        "ERROR" { Write-Host "  [X]  $Message" -ForegroundColor Red }
        default { Write-Host "  [*]  $Message" -ForegroundColor Cyan }
    }
}

function Find-SignTool {
    <#
    .SYNOPSIS
        Locate signtool.exe from the Windows SDK.
    #>

    # Check PATH first
    $inPath = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($inPath) {
        return $inPath.Source
    }

    # Search Windows SDK directories
    $sdkRoots = @(
        "${env:ProgramFiles(x86)}\Windows Kits\10\bin",
        "${env:ProgramFiles}\Windows Kits\10\bin",
        "${env:ProgramFiles(x86)}\Microsoft SDKs\ClickOnce\SignTool"
    )

    foreach ($root in $sdkRoots) {
        if (Test-Path $root) {
            $candidates = Get-ChildItem -Path $root -Recurse -Filter "signtool.exe" -ErrorAction SilentlyContinue |
                Where-Object { $_.FullName -match "x64" } |
                Sort-Object { $_.Directory.Name } -Descending
            if ($candidates) {
                return $candidates[0].FullName
            }
        }
    }

    return $null
}

function Get-OrCreateTestCert {
    <#
    .SYNOPSIS
        Find an existing self-signed code signing cert or create one for testing.
    #>

    $testSubject = "CN=OneClickBackup Test Signing"

    # Look for existing test cert
    $existing = Get-ChildItem Cert:\CurrentUser\My |
        Where-Object { $_.Subject -eq $testSubject -and $_.NotAfter -gt (Get-Date) } |
        Sort-Object NotAfter -Descending |
        Select-Object -First 1

    if ($existing) {
        Write-Status "Found existing test certificate: $($existing.Thumbprint)" "OK"
        return $existing.Thumbprint
    }

    # Offer to create one
    Write-Host ""
    Write-Status "No code signing certificate found." "WARN"
    Write-Host ""
    Write-Host "  No -CertThumbprint was provided and no self-signed test certificate exists."
    Write-Host "  A self-signed certificate is fine for local testing but Windows will still"
    Write-Host "  show SmartScreen warnings. For distribution, use a real certificate."
    Write-Host ""
    $response = Read-Host "  Create a self-signed test certificate? (y/N)"

    if ($response -notmatch "^[Yy]") {
        Write-Status "Aborted. No signing performed." "WARN"
        exit 0
    }

    Write-Status "Creating self-signed code signing certificate..."
    $cert = New-SelfSignedCertificate `
        -Subject $testSubject `
        -Type CodeSigningCert `
        -CertStoreLocation Cert:\CurrentUser\My `
        -NotAfter (Get-Date).AddYears(3) `
        -KeyAlgorithm RSA `
        -KeyLength 2048 `
        -HashAlgorithm SHA256

    Write-Status "Test certificate created: $($cert.Thumbprint)" "OK"
    Write-Host ""
    Write-Host "  Subject:     $($cert.Subject)"
    Write-Host "  Thumbprint:  $($cert.Thumbprint)"
    Write-Host "  Expires:     $($cert.NotAfter.ToString('yyyy-MM-dd'))"
    Write-Host ""

    # Trust the self-signed cert so local verification works
    Write-Status "To trust this cert locally, run (as Admin):" "WARN"
    Write-Host "    Export-Certificate -Cert Cert:\CurrentUser\My\$($cert.Thumbprint) -FilePath test_sign.cer"
    Write-Host "    Import-Certificate -FilePath test_sign.cer -CertStoreLocation Cert:\LocalMachine\Root"
    Write-Host ""

    return $cert.Thumbprint
}

function Invoke-SignTool {
    <#
    .SYNOPSIS
        Sign a single file with signtool.exe.
    #>
    param(
        [string]$SignToolPath,
        [string]$FilePath,
        [string]$Thumbprint,
        [string]$PfxFile,
        [string]$PfxPass,
        [string]$TsaUrl
    )

    if (-not (Test-Path $FilePath)) {
        Write-Status "File not found, skipping: $FilePath" "WARN"
        return $false
    }

    $fileName = Split-Path $FilePath -Leaf
    Write-Status "Signing $fileName ..."

    # Build signtool arguments
    $args = @("sign")

    if ($PfxFile) {
        # Sign with PFX file
        $args += "/f"
        $args += $PfxFile
        if ($PfxPass) {
            $args += "/p"
            $args += $PfxPass
        }
    }
    else {
        # Sign with certificate from store
        $args += "/sha1"
        $args += $Thumbprint
    }

    # Common options
    $args += "/fd"
    $args += "sha256"          # File digest algorithm
    $args += "/tr"
    $args += $TsaUrl           # RFC 3161 timestamp server
    $args += "/td"
    $args += "sha256"          # Timestamp digest algorithm
    $args += "/d"
    $args += "OneClick Backup" # Description shown in UAC prompts
    $args += "/v"              # Verbose
    $args += $FilePath

    $process = Start-Process -FilePath $SignToolPath -ArgumentList $args `
        -NoNewWindow -Wait -PassThru -RedirectStandardOutput "$env:TEMP\signtool_out.txt"

    $output = Get-Content "$env:TEMP\signtool_out.txt" -ErrorAction SilentlyContinue
    if ($output) {
        $output | ForEach-Object { Write-Host "       $_" }
    }

    if ($process.ExitCode -eq 0) {
        Write-Status "$fileName signed successfully." "OK"
        return $true
    }
    else {
        Write-Status "Failed to sign $fileName (exit code: $($process.ExitCode))." "ERROR"
        return $false
    }
}

function Test-Signature {
    <#
    .SYNOPSIS
        Verify the Authenticode signature on a file.
    #>
    param([string]$FilePath)

    if (-not (Test-Path $FilePath)) { return }

    $sig = Get-AuthenticodeSignature -FilePath $FilePath
    $fileName = Split-Path $FilePath -Leaf

    switch ($sig.Status) {
        "Valid" {
            Write-Status "$fileName - signature VALID (signer: $($sig.SignerCertificate.Subject))" "OK"
        }
        "NotSigned" {
            Write-Status "$fileName - NOT SIGNED" "WARN"
        }
        default {
            Write-Status "$fileName - signature status: $($sig.Status)" "WARN"
        }
    }
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "  ========================================"
Write-Host "    OneClick Backup - Code Signing Tool"
Write-Host "  ========================================"
Write-Host ""

# 1. Find signtool.exe
$signtool = Find-SignTool
if (-not $signtool) {
    Write-Status "signtool.exe not found." "ERROR"
    Write-Host ""
    Write-Host "  Install the Windows SDK to get signtool.exe:"
    Write-Host "    https://developer.microsoft.com/en-us/windows/downloads/windows-sdk/"
    Write-Host ""
    Write-Host "  Or install via Visual Studio Installer:"
    Write-Host "    Individual Components > Windows SDK Signing Tools"
    Write-Host ""
    exit 1
}
Write-Status "signtool.exe: $signtool" "OK"

# 2. Resolve the certificate
$thumbprint = $CertThumbprint
$pfxFile = $PfxPath

if (-not $thumbprint -and -not $pfxFile) {
    # No cert specified -- look for or create a self-signed test cert
    $thumbprint = Get-OrCreateTestCert
}

if ($thumbprint) {
    Write-Status "Certificate thumbprint: $thumbprint" "OK"
}
elseif ($pfxFile) {
    if (-not (Test-Path $pfxFile)) {
        Write-Status "PFX file not found: $pfxFile" "ERROR"
        exit 1
    }
    Write-Status "PFX file: $pfxFile" "OK"
}

# 3. Sign the files
Write-Host ""
$allOk = $true

# Sign main EXE
$result = Invoke-SignTool `
    -SignToolPath $signtool `
    -FilePath $ExePath `
    -Thumbprint $thumbprint `
    -PfxFile $pfxFile `
    -PfxPass $PfxPassword `
    -TsaUrl $TimestampServer

if (-not $result) { $allOk = $false }

# Sign installer (if it exists)
$result = Invoke-SignTool `
    -SignToolPath $signtool `
    -FilePath $InstallerPath `
    -Thumbprint $thumbprint `
    -PfxFile $pfxFile `
    -PfxPass $PfxPassword `
    -TsaUrl $TimestampServer

if (-not $result) { $allOk = $false }

# 4. Verify signatures
Write-Host ""
Write-Host "  Verification"
Write-Host "  ----------------------------------------"
Test-Signature -FilePath $ExePath
Test-Signature -FilePath $InstallerPath

# 5. Summary
Write-Host ""
Write-Host "  ========================================"
if ($allOk) {
    Write-Status "All files signed successfully." "OK"
}
else {
    Write-Status "Some files could not be signed. See output above." "WARN"
}
Write-Host "  ========================================"
Write-Host ""
