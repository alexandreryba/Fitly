<#
Assistant de configuration des sources a cle (Google Jobs, Adzuna, Jooble, France Travail, alertes Gmail).
Lance-le depuis la racine du projet :   .\scripts\configurer_sources.ps1
Pour chaque service : la page d'inscription s'ouvre, tu colles la cle (saisie masquee), elle est
enregistree dans tes variables d'environnement Windows (jamais dans un fichier du projet), puis testee.
#>
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$env:PYTHONIOENCODING = "utf-8"

function Read-Secret([string]$Prompt) {
    $secure = Read-Host -Prompt $Prompt -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr).Trim() }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

function Save-Key([string]$Name, [string]$Value) {
    if (-not $Value) { Write-Host "  (vide : $Name non modifie)"; return $false }
    [Environment]::SetEnvironmentVariable($Name, $Value, "User")   # persistent, like setx
    Set-Item -Path "Env:$Name" -Value $Value                        # usable right now in this window
    Write-Host "  $Name enregistre."
    return $true
}

function Test-Sources {
    Write-Host ""
    Write-Host "Test en direct (une petite requete par source configuree) :"
    & $Python -m fitly check-sources --live
}

$services = [ordered]@{
    "1" = "Google Jobs (via SerpApi)  - le plus utile, couvre LinkedIn/Indeed/HelloWork/WTTJ"
    "2" = "Adzuna                     - agregateur, gratuit"
    "3" = "Jooble                     - agregateur, cle envoyee par e-mail"
    "4" = "France Travail             - officiel, gratuit"
    "5" = "Alertes e-mail Gmail       - LinkedIn, WTTJ, Indeed, APEC... lus automatiquement"
    "6" = "Seulement tester ce qui est deja configure"
    "Q" = "Quitter"
}

while ($true) {
    Write-Host ""
    Write-Host "=== Fitly : configuration des sources ==="
    foreach ($k in $services.Keys) { Write-Host " $k. $($services[$k])" }
    $choice = (Read-Host "Ton choix").Trim().ToUpper()
    switch ($choice) {
        "1" {
            Write-Host ""
            Write-Host "1) Cree un compte sur la page qui s'ouvre (bouton Register), confirme ton e-mail."
            Write-Host "2) Une fois connecte, va dans 'Api Key' : copie 'Your Private API Key'."
            Start-Process "https://serpapi.com/users/sign_up"
            Read-Host "Appuie sur Entree quand tu as copie ta cle" | Out-Null
            Start-Process "https://serpapi.com/manage-api-key"
            if (Save-Key "SERPAPI_KEY" (Read-Secret "Colle la cle SerpApi (rien ne s'affiche, c'est normal)")) { Test-Sources }
        }
        "2" {
            Write-Host ""
            Write-Host "1) Cree un compte sur la page qui s'ouvre (Register)."
            Write-Host "2) Dans ton tableau de bord, 'API Access Details' : copie Application ID puis Application Key."
            Start-Process "https://developer.adzuna.com/signup"
            Read-Host "Appuie sur Entree quand tu vois tes identifiants" | Out-Null
            $id = (Read-Host "Colle l'Application ID").Trim()
            $ok1 = Save-Key "ADZUNA_APP_ID" $id
            $ok2 = Save-Key "ADZUNA_APP_KEY" (Read-Secret "Colle l'Application Key (rien ne s'affiche)")
            if ($ok1 -and $ok2) { Test-Sources }
        }
        "3" {
            Write-Host ""
            Write-Host "1) Remplis le formulaire de demande de cle sur la page qui s'ouvre (usage : recherche d'emploi personnelle)."
            Write-Host "2) La cle arrive par e-mail (parfois apres quelques heures). Relance ce choix quand tu l'as."
            Start-Process "https://jooble.org/api/about"
            if (Save-Key "JOOBLE_API_KEY" (Read-Secret "Colle la cle Jooble si tu l'as deja (sinon Entree)")) { Test-Sources }
        }
        "4" {
            Write-Host ""
            Write-Host "1) Cree un compte puis connecte-toi sur la page qui s'ouvre."
            Write-Host "2) 'Mes applications' > 'Creer une application' (nom : Fitly)."
            Write-Host "3) Ajoute l'API 'Offres d'emploi v2' a l'application."
            Write-Host "4) Copie l'identifiant client et la cle secrete."
            Start-Process "https://francetravail.io/data/api/offres-emploi"
            Read-Host "Appuie sur Entree quand tu as tes identifiants" | Out-Null
            $ok1 = Save-Key "FT_CLIENT_ID" ((Read-Host "Colle l'identifiant client").Trim())
            $ok2 = Save-Key "FT_CLIENT_SECRET" (Read-Secret "Colle la cle secrete (rien ne s'affiche)")
            if ($ok1 -and $ok2) { Test-Sources }
        }
        "5" {
            Write-Host ""
            Write-Host "Prerequis : validation en deux etapes active sur ton compte Google."
            Write-Host "1) Sur la page qui s'ouvre, cree un mot de passe d'application nomme Fitly."
            Write-Host "2) Copie les 16 caracteres affiches."
            Start-Process "https://myaccount.google.com/apppasswords"
            Read-Host "Appuie sur Entree quand tu as le mot de passe d'application" | Out-Null
            $user = (Read-Host "Ton adresse Gmail").Trim()
            $pw = (Read-Secret "Colle le mot de passe d'application (rien ne s'affiche)") -replace "\s", ""
            if ($user -and (Save-Key "FITLY_IMAP_PASSWORD" $pw)) {
                # Personal copy in config\local (never versioned), created from the shared default.
                $cfgPath = Join-Path $ProjectRoot "config\local\email_alerts.json"
                if (-not (Test-Path $cfgPath)) {
                    New-Item -ItemType Directory -Force -Path (Split-Path $cfgPath) | Out-Null
                    Copy-Item (Join-Path $ProjectRoot "config\email_alerts.json") $cfgPath
                }
                $cfg = Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
                $cfg.enabled = $true
                $cfg.user = $user
                [IO.File]::WriteAllText($cfgPath, ($cfg | ConvertTo-Json -Depth 5), (New-Object System.Text.UTF8Encoding($false)))
                Write-Host "  config\local\email_alerts.json : lecture IMAP activee pour $user (lecture seule)."
                Write-Host "  Pense a creer tes alertes LinkedIn / WTTJ / Indeed (voir docs\sources.md, section 5)."
                Test-Sources
            }
        }
        "6" { Test-Sources }
        "Q" {
            Write-Host ""
            Write-Host "Termine. Ferme et rouvre PowerShell, puis relance l'interface (.\scripts\start_ui.ps1)"
            Write-Host "pour que les nouvelles cles soient prises en compte partout."
            exit 0
        }
        default { Write-Host "Choix inconnu." }
    }
}
