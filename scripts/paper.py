"""Run the paper stack with isolated DB, production UI and per-service credentials."""
import os
import shutil
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from scripts.env import ROOT,load
load(ROOT/'secrets/paper.env')
load()
os.environ['BROKER_BACKEND']='alpaca'
from scripts import dev

standalone=ROOT/'services/dashboard/.next/standalone'
if not (standalone/'server.js').exists():
    raise SystemExit('Build the dashboard first: make build')
shutil.copytree(ROOT/'services/dashboard/public',standalone/'public',dirs_exist_ok=True)
shutil.copytree(ROOT/'services/dashboard/.next/static',standalone/'.next/static',dirs_exist_ok=True)
os.environ['HOSTNAME']='127.0.0.1'
for i,(name,cmd,cwd) in enumerate(dev.PROCS):
    if name=='dashboard':dev.PROCS[i]=(name,['node','server.js'],standalone)
(ROOT/'data').mkdir(exist_ok=True)
(ROOT/'data/paper-stack.pid').write_text(str(os.getpid())+'\n')
dev.main()
