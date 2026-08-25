param(
    [string]$InputDir = "data/pdfs",
    [string]$OutputDir = "artifacts/digital"
)

python -m specter digital --input $InputDir --out $OutputDir --rtl-mode image
