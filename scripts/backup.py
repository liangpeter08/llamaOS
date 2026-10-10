"""Create a recoverable PostgreSQL + strategy backup; optional off-host rsync target."""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from scripts.env import ROOT,load
load(ROOT/'secrets/paper.env');load()
from common import db,audit


def main():
    from psycopg.conninfo import conninfo_to_dict
    parts=conninfo_to_dict(os.environ['DATABASE_URL'])
    dest=ROOT/'data/backups'/time.strftime('%Y%m%d-%H%M%S',time.gmtime())
    dest.mkdir(parents=True,mode=0o700)
    binary=os.environ.get('PG_DUMP') or shutil.which('pg_dump')
    if not binary:
        for p in ['/opt/homebrew/opt/postgresql@16/bin/pg_dump','/usr/local/opt/postgresql@16/bin/pg_dump']:
            if Path(p).exists():binary=p;break
    if not binary:raise RuntimeError('pg_dump is required for backups')
    env=os.environ.copy();env['PGPASSWORD']=parts.get('password','')
    subprocess.run([binary,'-h',parts.get('host','127.0.0.1'),'-p',parts.get('port','5432'),'-U',parts['user'],
                    '-Fc','-f',str(dest/'database.dump'),parts['dbname']],env=env,check=True)
    shutil.copytree(ROOT/'strategies',dest/'strategies')
    c=db.get();head=audit.verify(c)
    import json
    (dest/'audit-checkpoint.json').write_text(json.dumps(head))
    remote=os.environ.get('BACKUP_TARGET')
    if remote:subprocess.run(['rsync','-az',str(dest)+'/',remote.rstrip('/')+'/'+dest.name+'/'],check=True)
    db.kv_set(c,'backup.latest',{'ts':time.time(),'path':str(dest),'audit_ok':head['ok'],'off_host':bool(remote)})
    print('Backup saved:',dest)

if __name__=='__main__':main()
