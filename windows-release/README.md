# Windows Releases

This build script is used for official releases of CPython on Windows.
It is somewhat more complex than `Tools/msi/buildrelease.bat` because it uses additional parallelism,
and limits the use of our VM that contains the code signing certificate,
but fundamentally achieves the same output.

This script is designed to be run on Azure Pipelines.
Information about the syntax can be found at https://docs.microsoft.com/azure/devops/pipelines/

The current deployment is at https://dev.azure.com/Python/cpython/_build?definitionId=21
Chances are you don't have permission to do anything other than view builds.
If you do have permission, you also need to launch the VM with the build tools, including code signing certificate.
This VM is maintained by the Windows build manager and should be strictly controlled.

(Further documentation to be added as we find out what ought to be documented.)
