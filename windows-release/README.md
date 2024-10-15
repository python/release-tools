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

For code signing, we use [Azure Trusted Signing](https://learn.microsoft.com/en-us/azure/trusted-signing/overview).
This service belongs to the PSF's Azure subscription and is paid for on a monthly basis.
When we send files for signing, it uploads a manifest (hash) of the file rather than the file itself,
and then receives a signature that can be embedded into the target file.

Authentication to Azure currently uses an [Entra app registration](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app)
rather than OIDC (which is available, and may be switched to in future).
The authentication details are stored as private variables in a Variable group called CPythonSign.
Referencing this variable group is what triggers approvals during the build.
The group is at https://dev.azure.com/Python/cpython/_library?itemType=VariableGroups&view=VariableGroupView&variableGroupId=1&path=CPythonSign

The five variables in the Variable Group identify the Entra ID
 with access,
and the name of the certificate to use.

* `TrustedSigningClientId` - the "Application (client) ID" of the App registration
* `TrustedSigningTenantId` - the "Directory (tenant) ID" of the App registration
* `TrustedSigningSecret` - the current "Client secret" of the App registration
* `TrustedSigningUri` - the endpoint of the Trusted Signing service (provided by Azure)
* `TrustedSigningAccount` - the name of our Trusted Signing account, "pythondev". This is not a secret
* `TrustedSigningCertificateName` - the name of our certificate profile. This is not a secret

Certificates are renewed daily,
and as such it is no longer useful to reference the "thumbprint" (SHA1 hash) of the certificate.
Instead, to trust all of our releases in restricted scenarios,
you need to first trust one of the certificates in the certification path
and then check for EKU `1.3.6.1.4.1.311.97.608394634.79987812.305991749.578777327`,
which represents our signing account,
or Subject `CN=Python Software Foundation,O=Python Software Foundation,L=Beaverton,S=Oregon,C=US`.

TODO: Reference/link to documentation on verifying certificates with tools.

Note that regular signing checks (such as `signtool.exe verify /pa python.exe`)
and malware scans will treat the files as correctly signed.
It's only more complicated to verify that it was signed _specifically_ with our cert.

(Further documentation to be added as we find out what ought to be documented.)
