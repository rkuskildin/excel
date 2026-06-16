"""Веб-морда для Excel-агента: задача + загрузка/скачивание .xlsx, защита паролем.

Запуск (внутри контейнера):
    python webapp.py            # слушает 0.0.0.0:WEB_PORT (по умолчанию 8600)

Переменные окружения:
    WEB_USER       логин (по умолчанию admin)
    WEB_PASSWORD   пароль (ОБЯЗАТЕЛЬНО; без него запуск откажет)
    WEB_PORT       порт (по умолчанию 8600)
    WEB_PROFILE    профиль агента по умолчанию (skill)
    WEB_WORKDIR    рабочая папка (по умолчанию runs/web)
+ переменные модели из .env (OPENAI_BASE_URL, API_KEY, MODEL_NAME, ...).
"""
from __future__ import annotations

import os
import secrets
import shutil
import sys
import threading
from pathlib import Path

import uvicorn
from fastapi import (Depends, FastAPI, File, Form, HTTPException, UploadFile,
                     status)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

sys.path.insert(0, str(Path(__file__).parent / "src"))
from excel_agent.config import DATA_SOURCE  # noqa: E402

USER = os.getenv("WEB_USER", "admin")
PASSWORD = os.getenv("WEB_PASSWORD", "")
PORT = int(os.getenv("WEB_PORT", "8600"))
DEFAULT_PROFILE = os.getenv("WEB_PROFILE", "skill")
WORKDIR = Path(os.getenv("WEB_WORKDIR", "runs/web")).resolve()
WORKDIR.mkdir(parents=True, exist_ok=True)

if not PASSWORD:
    raise SystemExit("WEB_PASSWORD не задан — откажусь стартовать без пароля.")

app = FastAPI()
security = HTTPBasic()
run_lock = threading.Lock()  # агент не любит параллельные запуски (rate limit + общий workdir)


def auth(creds: HTTPBasicCredentials = Depends(security)) -> bool:
    ok = (secrets.compare_digest(creds.username, USER)
          and secrets.compare_digest(creds.password, PASSWORD))
    if not ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Неверный логин или пароль",
                            headers={"WWW-Authenticate": "Basic"})
    return True


def list_xlsx() -> list[dict]:
    out = []
    for f in sorted(WORKDIR.glob("*.xlsx")):
        out.append({"name": f.name, "size": f.stat().st_size})
    return out


@app.post("/api/upload")
def upload(_: bool = Depends(auth), file: UploadFile = File(...)):
    name = Path(file.filename).name
    if not name.lower().endswith(".xlsx"):
        raise HTTPException(400, "Только .xlsx")
    dest = WORKDIR / name
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    return {"ok": True, "files": list_xlsx()}


@app.post("/api/fresh")
def fresh(_: bool = Depends(auth)):
    for f in DATA_SOURCE.glob("*.xlsx"):
        shutil.copy2(f, WORKDIR / f.name)
    return {"ok": True, "files": list_xlsx()}


@app.get("/api/files")
def files(_: bool = Depends(auth)):
    return {"files": list_xlsx()}


@app.get("/download/{name}")
def download(name: str, _: bool = Depends(auth)):
    f = (WORKDIR / Path(name).name)
    if not f.exists():
        raise HTTPException(404, "нет файла")
    return FileResponse(f, filename=f.name)


@app.post("/api/run")
def run(_: bool = Depends(auth), task: str = Form(...),
        profile: str = Form(DEFAULT_PROFILE)):
    if not task.strip():
        raise HTTPException(400, "пустая задача")
    if profile not in ("baseline", "skill", "skill_subagent"):
        profile = DEFAULT_PROFILE
    if not run_lock.acquire(blocking=False):
        raise HTTPException(409, "агент уже выполняет задачу — подождите")
    try:
        # импорт тяжёлый — внутри, чтобы старт сервера был быстрым
        from excel_agent.agent import run_with_fallback
        # подсказываем агенту, какие файлы лежат в рабочей папке (иначе переспрашивает имя)
        files_now = list_xlsx()
        if files_now:
            hint = ("Файлы .xlsx в рабочей папке: "
                    + ", ".join(f"'{f['name']}'" for f in files_now)
                    + ". Если в задаче файл не назван явно и он один — работай с ним.\n\n")
            task = hint + task
        res = run_with_fallback(profile, WORKDIR, task)
        return JSONResponse({"answer": res.get("answer", ""),
                             "tokens": res.get("tokens"),
                             "steps": res.get("steps"),
                             "files": list_xlsx()})
    except Exception as e:  # noqa: BLE001 — показать причину (часто rate limit)
        return JSONResponse(status_code=502,
                            content={"error": f"{type(e).__name__}: {e}"[:1500]})
    finally:
        run_lock.release()


HTML = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Excel Агент</title>
<style>
  :root{
    --text:#eaf0ff; --muted:#9aa6c8; --line:#26304f; --acc:#19b56a; --acc2:#13d178;
    --card:rgba(22,29,52,.72); --danger:#ff6b6b; --radius:18px;
  }
  *{box-sizing:border-box}
  html,body{margin:0}
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Inter,Arial,sans-serif;
    color:var(--text); min-height:100vh; line-height:1.5;
    background:
      radial-gradient(900px 500px at 85% -8%, rgba(25,181,106,.18), transparent 60%),
      radial-gradient(800px 500px at 0% 0%, rgba(60,110,255,.16), transparent 55%),
      linear-gradient(180deg,#0a0f1f,#0a0e1a 60%);}
  .wrap{max-width:920px;margin:0 auto;padding:28px 18px 90px}
  header{display:flex;align-items:center;gap:14px;margin-bottom:6px}
  .logo{width:48px;height:48px;border-radius:14px;display:grid;place-items:center;font-size:26px;
    background:linear-gradient(145deg,#19b56a,#0e8a4f);box-shadow:0 8px 24px rgba(25,181,106,.35)}
  h1{font-size:1.55rem;margin:0;letter-spacing:-.5px}
  h1 .g{background:linear-gradient(90deg,#19b56a,#48e08b);-webkit-background-clip:text;background-clip:text;color:transparent}
  .tagline{color:var(--muted);font-size:.92rem;margin:2px 0 22px}
  .card{background:var(--card);backdrop-filter:blur(8px);border:1px solid var(--line);
    border-radius:var(--radius);padding:20px 20px;margin:16px 0;box-shadow:0 12px 40px rgba(0,0,0,.35)}
  .step{display:flex;align-items:center;gap:10px;font-weight:600;margin-bottom:14px;font-size:1.02rem}
  .num{width:24px;height:24px;border-radius:50%;display:grid;place-items:center;font-size:.8rem;
    background:rgba(25,181,106,.18);color:#48e08b;border:1px solid rgba(25,181,106,.4)}
  .drop{border:1.6px dashed #33406b;border-radius:14px;padding:22px;text-align:center;color:var(--muted);
    cursor:pointer;transition:.18s;background:rgba(255,255,255,.015)}
  .drop:hover,.drop.drag{border-color:var(--acc2);background:rgba(25,181,106,.08);color:var(--text)}
  .drop b{color:var(--acc2)}
  .row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
  .ghost{background:transparent;color:var(--text);border:1px solid var(--line);border-radius:10px;
    padding:9px 14px;font-size:.92rem;cursor:pointer;transition:.15s}
  .ghost:hover{border-color:var(--acc2);color:var(--acc2)}
  .files{margin-top:14px;display:flex;flex-direction:column;gap:8px}
  .file{display:flex;align-items:center;gap:12px;padding:11px 13px;border:1px solid var(--line);
    border-radius:12px;background:rgba(255,255,255,.02)}
  .file .ic{font-size:20px}
  .file .nm{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .file .sz{color:var(--muted);font-size:.82rem;margin-left:6px}
  .dlbtn{background:rgba(25,181,106,.14);color:#48e08b;border:1px solid rgba(25,181,106,.4);
    border-radius:9px;padding:7px 13px;font-size:.86rem;cursor:pointer;white-space:nowrap}
  .dlbtn:hover{background:rgba(25,181,106,.26)}
  .empty{color:var(--muted);font-size:.9rem;text-align:center;padding:8px}
  textarea{width:100%;min-height:96px;resize:vertical;background:rgba(8,12,24,.65);color:var(--text);
    border:1px solid var(--line);border-radius:12px;padding:13px 14px;font-size:1rem;font-family:inherit;line-height:1.5}
  textarea:focus{outline:none;border-color:var(--acc2);box-shadow:0 0 0 3px rgba(25,181,106,.15)}
  .seg{display:inline-flex;background:rgba(8,12,24,.6);border:1px solid var(--line);border-radius:11px;padding:3px}
  .seg button{background:transparent;border:0;color:var(--muted);padding:8px 14px;border-radius:8px;
    font-size:.9rem;cursor:pointer;transition:.15s}
  .seg button.on{background:linear-gradient(145deg,#19b56a,#0e8a4f);color:#04130b;font-weight:600}
  .controls{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin-top:14px}
  .run{background:linear-gradient(145deg,#19b56a,#11c06c);color:#04130b;border:0;border-radius:12px;
    padding:12px 26px;font-size:1rem;font-weight:700;cursor:pointer;box-shadow:0 8px 22px rgba(25,181,106,.32);
    display:inline-flex;align-items:center;gap:9px;transition:.15s}
  .run:hover{filter:brightness(1.06)}
  .run:disabled{opacity:.6;cursor:wait;box-shadow:none}
  .spin{width:15px;height:15px;border:2px solid rgba(4,19,11,.35);border-top-color:#04130b;border-radius:50%;
    animation:sp .7s linear infinite;display:none}
  .run.busy .spin{display:inline-block}
  @keyframes sp{to{transform:rotate(360deg)}}
  .hint{color:var(--muted);font-size:.85rem}
  #out{display:none}
  .answer{white-space:pre-wrap;line-height:1.6;background:rgba(8,12,24,.55);border:1px solid var(--line);
    border-left:3px solid var(--acc2);border-radius:12px;padding:14px 16px}
  .badges{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
  .badge{font-size:.8rem;color:var(--muted);background:rgba(255,255,255,.04);border:1px solid var(--line);
    border-radius:999px;padding:5px 11px}
  .err{color:var(--danger)}
  .examples{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
  .chip{font-size:.82rem;color:var(--muted);background:rgba(255,255,255,.03);border:1px solid var(--line);
    border-radius:999px;padding:6px 12px;cursor:pointer;transition:.15s}
  .chip:hover{border-color:var(--acc2);color:var(--acc2)}
  #toast{position:fixed;left:50%;bottom:26px;transform:translateX(-50%) translateY(40px);opacity:0;
    background:#11182e;border:1px solid var(--line);color:var(--text);padding:12px 18px;border-radius:12px;
    box-shadow:0 12px 40px rgba(0,0,0,.5);transition:.25s;max-width:90vw;font-size:.9rem;pointer-events:none}
  #toast.show{transform:translateX(-50%) translateY(0);opacity:1}
  #toast.bad{border-color:var(--danger)}
  footer{text-align:center;color:var(--muted);font-size:.8rem;margin-top:26px;opacity:.7}
</style></head><body>
<div class="wrap">
  <header>
    <div class="logo">📊</div>
    <div>
      <h1>Excel <span class="g">Агент</span></h1>
    </div>
  </header>
  <div class="tagline">Загрузи таблицу, опиши задачу — агент правит .xlsx через pandas/openpyxl и отдаёт результат.</div>

  <div class="card">
    <div class="step"><span class="num">1</span> Данные</div>
    <div id="drop" class="drop">
      Перетащи сюда <b>.xlsx</b> или <b>нажми</b>, чтобы выбрать файл
      <input type="file" id="up" accept=".xlsx" hidden>
    </div>
    <div class="row" style="margin-top:12px">
      <button class="ghost" onclick="fresh()">✨ Загрузить демо-данные</button>
      <button class="ghost" onclick="refresh()">↻ Обновить список</button>
    </div>
    <div id="files" class="files"></div>
  </div>

  <div class="card">
    <div class="step"><span class="num">2</span> Задача</div>
    <textarea id="task" placeholder="Например: на листе 'JV Model' сделай заголовки жирными с серой заливкой, числа в формат #,##0, проценты — 0.0%"></textarea>
    <div class="examples">
      <span class="chip" onclick="ex(this)">Очисти столбец 'Выручка (грязная)' и добавь числовой 'Выручка'</span>
      <span class="chip" onclick="ex(this)">Удали строки-дубликаты</span>
      <span class="chip" onclick="ex(this)">Построй нативную диаграмму по сводке</span>
    </div>
    <div class="controls">
      <div class="seg" id="seg">
        <button class="on" data-v="skill">skill</button>
        <button data-v="baseline">baseline</button>
        <button data-v="skill_subagent">skill+субагент</button>
      </div>
      <button id="go" class="run" onclick="run()"><span class="spin"></span><span id="gotext">Выполнить</span></button>
      <span id="status" class="hint"></span>
    </div>
  </div>

  <div class="card" id="out">
    <div class="step"><span class="num">✓</span> Результат</div>
    <div id="answer"></div>
    <div class="badges" id="badges"></div>
  </div>

  <footer>Excel deep-agent · DeepAgents + openpyxl · защищено паролем и TLS</footer>
</div>
<div id="toast"></div>
<script>
let profile='skill';
function esc(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML}
function toast(msg,bad){const t=document.getElementById('toast');t.textContent=msg;t.className='show'+(bad?' bad':'');
  clearTimeout(t._t);t._t=setTimeout(()=>t.className='',3200)}
async function j(u,o){const r=await fetch(u,o);const d=await r.json().catch(()=>({}));
  if(!r.ok)throw new Error(d.error||d.detail||('HTTP '+r.status));return d}
function fileIcon(n){return '📄'}
function renderFiles(fs){const el=document.getElementById('files');
  if(!fs||!fs.length){el.innerHTML='<div class="empty">Файлов пока нет — загрузи демо или свой .xlsx</div>';return}
  el.innerHTML=fs.map(f=>`<div class="file"><span class="ic">${fileIcon(f.name)}</span>
    <span class="nm">${esc(f.name)}<span class="sz">${(f.size/1024).toFixed(1)} КБ</span></span>
    <button class="dlbtn" onclick="dl('${encodeURIComponent(f.name)}','${esc(f.name).replace(/'/g,"\\'")}')">⬇ Скачать</button></div>`).join('')}
async function dl(enc,name){try{
    const r=await fetch('download/'+enc);if(!r.ok)throw new Error('HTTP '+r.status);
    const b=await r.blob();const u=URL.createObjectURL(b);const a=document.createElement('a');
    a.href=u;a.download=name;document.body.appendChild(a);a.click();a.remove();
    setTimeout(()=>URL.revokeObjectURL(u),1500);toast('Скачивается: '+name);
  }catch(e){toast('Не удалось скачать: '+e.message,true)}}
async function refresh(){try{const d=await j('api/files');renderFiles(d.files)}catch(e){toast(e.message,true)}}
async function fresh(){try{const d=await j('api/fresh',{method:'POST'});renderFiles(d.files);toast('Демо-данные загружены')}catch(e){toast(e.message,true)}}
async function send(file){const fd=new FormData();fd.append('file',file);
  try{const d=await j('api/upload',{method:'POST',body:fd});renderFiles(d.files);toast('Загружено: '+file.name)}catch(e){toast(e.message,true)}}
function ex(el){document.getElementById('task').value=el.textContent.trim()}
// drag & drop + click upload
const drop=document.getElementById('drop'),up=document.getElementById('up');
drop.onclick=()=>up.click();
up.onchange=()=>{if(up.files[0])send(up.files[0]);up.value=''};
['dragenter','dragover'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.add('drag')}));
['dragleave','drop'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.remove('drag')}));
drop.addEventListener('drop',ev=>{const f=ev.dataTransfer.files[0];if(f)send(f)});
// profile segmented control
document.querySelectorAll('#seg button').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('#seg button').forEach(x=>x.classList.remove('on'));
  b.classList.add('on');profile=b.dataset.v});
async function run(){const task=document.getElementById('task').value.trim();
  if(!task){toast('Впиши задачу',true);return}
  const go=document.getElementById('go');go.disabled=true;go.classList.add('busy');
  document.getElementById('gotext').textContent='Агент работает…';
  document.getElementById('status').textContent='может занять минуту-другую';
  const out=document.getElementById('out');out.style.display='block';
  document.getElementById('answer').innerHTML='<div class="answer">⏳ агент думает…</div>';
  document.getElementById('badges').innerHTML='';
  const fd=new FormData();fd.append('task',task);fd.append('profile',profile);
  try{const d=await j('api/run',{method:'POST',body:fd});
    document.getElementById('answer').innerHTML='<div class="answer">'+esc(d.answer||'(пустой ответ)')+'</div>';
    document.getElementById('badges').innerHTML=
      '<span class="badge">🧩 профиль: '+profile+'</span>'+
      '<span class="badge">🔢 токены: '+(d.tokens??'?')+'</span>'+
      '<span class="badge">💬 сообщений: '+(d.steps??'?')+'</span>';
    renderFiles(d.files);document.getElementById('status').textContent='готово';toast('Готово ✓');
  }catch(e){document.getElementById('answer').innerHTML='<div class="answer err">⚠ '+esc(e.message)+'</div>';
    document.getElementById('status').textContent='ошибка';toast(e.message,true)}
  go.disabled=false;go.classList.remove('busy');document.getElementById('gotext').textContent='Выполнить'}
refresh();
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index(_: bool = Depends(auth)):
    return HTML


if __name__ == "__main__":
    ssl_cert = os.getenv("WEB_SSL_CERT", "")
    ssl_key = os.getenv("WEB_SSL_KEY", "")
    use_tls = bool(ssl_cert and ssl_key and Path(ssl_cert).exists() and Path(ssl_key).exists())
    scheme = "https" if use_tls else "http"
    print(f"Excel-агент web: {scheme}://0.0.0.0:{PORT} "
          f"(user={USER}, profile={DEFAULT_PROFILE}, tls={'on' if use_tls else 'off'})")
    kwargs = dict(host="0.0.0.0", port=PORT, log_level="warning")
    if use_tls:
        kwargs.update(ssl_certfile=ssl_cert, ssl_keyfile=ssl_key)
    uvicorn.run(app, **kwargs)
