# Windows Releases

This build script is used for official releases of CPython on Windows.
It is somewhat more complex than `Tools/msi/buildrelease.bat` because it uses additional parallelism
and uses our official code signing certificate.

This script is designed to be run on Azure Pipelines.
Information about the syntax can be found at https://docs.microsoft.com/azure/devops/pipelines/

The current deployment is at https://dev.azure.com/Python/cpython/_build?definitionId=21
Chances are you don't have permission to do anything other than view builds. Access is controlled by the release team.

If you do have permission, you can launch a release build by selecting **Run pipeline**,
specify the desired **Git remote** and **Git tag**, enable **Publish release**,
toggle any version specific options, and click **Run**.

The version specific options are required due to changes in our build that require modifications
to the publish pipeline. For example, whether to publish ARM64 binaries.

When signing is enabled (any value besides "Unsigned"), authorised approvers will be notified and
will need to approve each stage that requires the signing certificate (typically three).
This helps prevent "surprise" builds from using the official certificate.

Some additional points to be aware of:

* packages are not automatically published to the Microsoft Store
* successful builds should be retained by selecting "Retain" under the "..." menu in the top-right

The `msixupload` artifacts should be uploaded to the Microsoft Store at
https://partner.microsoft.com/en-us/dashboard/apps-and-games/overview.
Access to this site is very limited.
We also usually update the screenshots so that the version information they show matches the release.

Azure DevOps no longer has a per-pipeline option for retention,
and so the only way to permanently retain a build is to manually select the "Retain" option.
Without this, the build records will be lost after 30 days.

## Finding/updating certificates

The code signing certificate is stored in Azure Key Vault, and is authenticated using the
variables in a Variable group called CPythonSign. The variable group is what triggers approvals.
The group is at https://dev.azure.com/Python/cpython/_library?itemType=VariableGroups&view=VariableGroupView&variableGroupId=1&path=CPythonSign
A second group called CPythonTestSign exists without approvals, but only has access to a test signing certificate.

The five variables in the Variable Group identify the Entra ID
[App registration](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app) with access,
and the name of the certificate to use.

* `KeyVaultApplication` - the "Application (client) ID" of the App registration
* `KeyVaultDirectory` - the "Directory (tenant) ID" of the App registration
* `KeyVaultSecret` - the current "Client secret" of the App registration
* `KeyVaultUri` - the base `https://*.vault.azure.net/` URI of the Key Vault
* `KeyVaultCertificateName` - the name of the certificate. This is not a secret

The Key Vault should be configured to use Azure role-based access control (soon to be the only option),
and the App registration should have the "Key Vault Certificate User" and "Key Vault Crypto User" roles.
The trusted owner of the Key Vault should have the "Owner" role, but the App registration should not.

To upload a new code signing certificate (which will be provided by the PSF),
you need the certificate in encrypted .pfx format.
This can then be uploaded directly through the Azure Portal into the Key Vault along with the passphrase.
If reusing an existing Key Vault, upload it as a new version of the existing certificate.
If it is uploaded as a new certificate, the Variable Group must be updated.

GPG signature generation uses a GPG key stored in the Secure Files library.
This can be found at https://dev.azure.com/Python/cpython/_library?itemType=SecureFiles
Regardless of who triggers the build, the signatures will be attributed to whoever's key is used.
The passphrase for the key is a secure build variable,
and can be modified by editing the build definition and selecting **Variables**.
(TODO: Move the passphrase and reference to the file into the CPythonSign variable group.)

(Further documentation to be added as we find out what ought to be documented.)
