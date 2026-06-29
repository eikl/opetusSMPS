
from pathlib import Path
root = Path("/run/user/1000/gvfs/smb-share:server=group5.ad.helsinki.fi,share=h527/smear/helsinki/particle/smps/rawdata/2026")
files = sorted(root.glob("*.sum"))
print(files)