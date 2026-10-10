"""Durable alerts and daily backups, separate from the order executor."""
import json
import logging
import os
import signal
import smtplib
import ssl
import subprocess
import sys
import time
from email.message import EmailMessage
from common import db

SCHEMA="""CREATE TABLE IF NOT EXISTS alerts(id BIGSERIAL PRIMARY KEY,audit_id BIGINT UNIQUE,ts DOUBLE PRECISION,kind TEXT,message TEXT,status TEXT DEFAULT 'pending',error TEXT);"""


def main():
    from scripts.env import ROOT,load
    load(ROOT/'secrets/alerts.env')
    c=db.get();db.init(c);c.script(SCHEMA);stopped=False
    def stop(*_):
        nonlocal stopped
        stopped=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    while not stopped:
        try:
            for event in db.rows(c,"SELECT a.* FROM audit a LEFT JOIN alerts n ON n.audit_id=a.id WHERE n.id IS NULL AND a.kind IN ('intent.pending','paper.fill','kill_switch.triggered','paper.order_rejected','paper.watchdog_flatten') ORDER BY a.id LIMIT 100"):
                message=f"llamaOS {event['kind']}: {event['payload']}"
                c.execute("INSERT INTO alerts(audit_id,ts,kind,message) VALUES (?,?,?,?) ON CONFLICT(audit_id) DO NOTHING",(event['id'],event['ts'],event['kind'],message))
            telegram=bool(os.environ.get('TELEGRAM_TOKEN') and os.environ.get('TELEGRAM_CHAT_ID'))
            email=bool(os.environ.get('SMTP_HOST') and os.environ.get('ALERT_EMAIL_TO'))
            if telegram or email:
                for alert in db.rows(c,"SELECT * FROM alerts WHERE status='pending' ORDER BY id LIMIT 10"):
                    try:
                        if telegram:
                            import httpx
                            r=httpx.post('https://api.telegram.org/bot'+os.environ['TELEGRAM_TOKEN']+'/sendMessage',json={'chat_id':os.environ['TELEGRAM_CHAT_ID'],'text':alert['message']},timeout=10)
                            if not r.is_success or not r.json().get('ok'):raise RuntimeError('Telegram delivery failed')
                        else:
                            message=EmailMessage();message['From']=os.environ['ALERT_EMAIL_FROM'];message['To']=os.environ['ALERT_EMAIL_TO'];message['Subject']='llamaOS '+alert['kind'];message.set_content(alert['message'])
                            with smtplib.SMTP_SSL(os.environ['SMTP_HOST'],int(os.environ.get('SMTP_PORT','465')),context=ssl.create_default_context(),timeout=10) as smtp:
                                if os.environ.get('SMTP_USER'):smtp.login(os.environ['SMTP_USER'],os.environ['SMTP_PASSWORD'])
                                smtp.send_message(message)
                        c.execute("UPDATE alerts SET status='sent',error=NULL WHERE id=?",(alert['id'],))
                    except Exception as e:
                        c.execute("UPDATE alerts SET error=? WHERE id=?",(type(e).__name__,alert['id']))
            latest=db.kv_get(c,'backup.latest') or {}
            attempted=db.kv_get(c,'backup.attempted') or 0
            if time.time()-latest.get('ts',0)>86400 and time.time()-attempted>300:
                db.kv_set(c,'backup.attempted',time.time())
                result=subprocess.run([sys.executable,'-m','scripts.backup'],capture_output=True,timeout=120)
                if result.returncode:db.kv_set(c,'backup.error',{'ts':time.time(),'type':'backup failed'})
            db.kv_set(c,'health.operations',{'ts':time.time(),'notifications_configured':telegram or email})
        except Exception as e:
            logging.error('Operations retry: %s',type(e).__name__)
        for _ in range(10):
            if stopped:break
            time.sleep(1)

if __name__=='__main__':main()
