#!/usr/bin/pwsh
param(
    [string]$Organization,
    [string]$AppId,
    [string]$CertificatePath,
    [string]$CertificatePassword = $null,
    [string]$TargetAddress,
    [string]$TargetName,
    [string]$SenderAddress
)

if ($CertificatePassword) {
    $SecureCertificatePassword = ( $CertificatePassword | ConvertTo-SecureString -AsPlainText -Force )
    Connect-ExchangeOnline -AppId $AppId -Organization $Organization -CertificateFilePath $CertificatePath -CertificatePassword $SecureCertificatePassword
} else {
    Connect-ExchangeOnline -AppId $AppId -Organization $Organization -CertificateFilePath $CertificatePath
}

New-Mailbox -Shared -Name $TargetAddress -DisplayName $TargetName -PrimarySmtpAddress $TargetAddress
Add-RecipientPermission -Identity $TargetAddress -Trustee $SenderAddress -AccessRights SendAs -Confirm:$false

Disconnect-ExchangeOnline -Confirm:$false
