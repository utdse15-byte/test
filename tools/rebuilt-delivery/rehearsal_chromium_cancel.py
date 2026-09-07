"""Cancel a real owned Chromium screenshot, without claiming screenshot success."""
from pathlib import Path
import hashlib,json,os,subprocess,sys,threading,time
import psutil
from manju.media.html_card import render_card_png
from manju.media.ffmpeg import cancel_scope,MediaCanceled
root=Path(sys.argv[1]);root.mkdir(exist_ok=False)
dest=root/'old-cover.png';dest.write_bytes(b'old cover bytes must survive a canceled replacement\n')
old=hashlib.sha256(dest.read_bytes()).hexdigest();event=threading.Event();seen=[];signaled=[None]
other=subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)'])
def watch():
 deadline=time.monotonic()+8
 while time.monotonic()<deadline:
  for child in psutil.Process(os.getpid()).children(recursive=True):
   try:
    if any(arg.startswith('--screenshot=') for arg in child.cmdline()):
     seen.extend([(x.pid,x.create_time()) for x in [child,*child.children(recursive=True)]]);signaled[0]=time.monotonic();event.set();return
   except psutil.Error:pass
  time.sleep(.02)
 event.set()
thread=threading.Thread(target=watch,daemon=True);thread.start();started=time.monotonic()
try:
 try:
  with cancel_scope(event.is_set):render_card_png('实际 Chromium 取消演练',dest,width=320,height=180,chromium=Path('/usr/bin/chromium'))
 except MediaCanceled:outcome='canceled'
 else:outcome='completed_before_cancel'
 elapsed=time.monotonic()-started;thread.join(timeout=1)
 def still_running(item):
  try:
   p=psutil.Process(item[0]);return p.create_time()==item[1] and p.status()!=psutil.STATUS_ZOMBIE
  except psutil.Error:return False
 deadline=time.monotonic()+3
 while any(still_running(x) for x in seen) and time.monotonic()<deadline:time.sleep(.05)
 report={'ok':outcome=='canceled' and bool(seen) and hashlib.sha256(dest.read_bytes()).hexdigest()==old and other.poll() is None and not any(still_running(x) for x in seen),
 'outcome':outcome,'actual_screenshot_process_observed':bool(seen),'observed_owned_processes':len(seen),
 'total_elapsed_seconds':elapsed,'cancel_response_seconds':None if signaled[0] is None else started+elapsed-signaled[0],
 'old_cover_bytes_unchanged':hashlib.sha256(dest.read_bytes()).hexdigest()==old,
 'unrelated_process_survived':other.poll() is None,'observed_owned_processes_stopped':not any(still_running(x) for x in seen),
 'screenshot_rendering_success_claimed':False,'native_windows_acceptance':False}
 (root/'RESULT.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False,indent=2));assert report['ok'],report
finally:
 other.terminate();other.wait(timeout=5)
