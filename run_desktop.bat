@echo off
rem bladeRF Workbench (desktop edition) from source: needs 64-bit Python + numpy.
py -3 -m pip install --quiet --disable-pip-version-check "numpy<3"
start "" pyw -3 "%~dp0bladeRF_Workbench_Desktop.py" %*
