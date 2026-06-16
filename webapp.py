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

import json
import os
import re
import secrets
import shutil
import sys
import threading
from pathlib import Path

import uvicorn
import yaml
from fastapi import (Depends, FastAPI, File, Form, HTTPException, UploadFile,
                     status)
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               StreamingResponse)
from fastapi.security import HTTPBasic, HTTPBasicCredentials

sys.path.insert(0, str(Path(__file__).parent / "src"))
from excel_agent.config import DATA_SOURCE  # noqa: E402

USER = os.getenv("WEB_USER", "admin")
PASSWORD = os.getenv("WEB_PASSWORD", "")
PORT = int(os.getenv("WEB_PORT", "8600"))
DEFAULT_PROFILE = os.getenv("WEB_PROFILE", "skill")
WORKDIR = Path(os.getenv("WEB_WORKDIR", "runs/web")).resolve()
WORKDIR.mkdir(parents=True, exist_ok=True)
# Пользовательские скиллы (config.skills_dirs читает ту же переменную EXTRA_SKILLS_DIR).
SKILLS_UP = Path(os.getenv("EXTRA_SKILLS_DIR", str(WORKDIR.parent / "skills"))).resolve()
SKILLS_UP.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("EXTRA_SKILLS_DIR", str(SKILLS_UP))

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


def list_skills() -> list[dict]:
    out = []
    for md in sorted(SKILLS_UP.glob("*/SKILL.md")):
        item = {"name": md.parent.name, "description": ""}
        try:
            meta = yaml.safe_load(md.read_text(encoding="utf-8").split("---")[1])
            if isinstance(meta, dict):
                item["name"] = str(meta.get("name", md.parent.name))
                item["description"] = str(meta.get("description", ""))[:200]
        except Exception:  # noqa: BLE001
            item["description"] = "(не удалось прочитать заголовок)"
        out.append(item)
    return out


@app.get("/api/skills")
def skills(_: bool = Depends(auth)):
    return {"skills": list_skills()}


@app.post("/api/skill")
def upload_skill(_: bool = Depends(auth), file: UploadFile = File(...)):
    raw = file.file.read().decode("utf-8", "replace")
    parts = raw.split("---")
    if len(parts) < 3:
        raise HTTPException(400, "Нет YAML-заголовка (--- … ---) в начале SKILL.md")
    try:
        meta = yaml.safe_load(parts[1])
    except yaml.YAMLError as e:
        raise HTTPException(400, f"Битый YAML в заголовке: {e}")
    if not isinstance(meta, dict) or not meta.get("name") or not meta.get("description"):
        raise HTTPException(400, "В YAML-заголовке обязательны поля name и description")
    name = re.sub(r"[^a-z0-9_-]", "-", str(meta["name"]).strip().lower())[:40] or "skill"
    skill_dir = SKILLS_UP / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(raw, encoding="utf-8")
    return {"ok": True, "name": name, "skills": list_skills()}


@app.post("/api/skill/delete")
def delete_skill(_: bool = Depends(auth), name: str = Form(...)):
    d = SKILLS_UP / Path(name).name
    if d.is_dir():
        shutil.rmtree(d)
    return {"ok": True, "skills": list_skills()}


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


@app.post("/api/run_stream")
def run_stream(_: bool = Depends(auth), task: str = Form(...),
               profile: str = Form(DEFAULT_PROFILE)):
    if not task.strip():
        raise HTTPException(400, "пустая задача")
    if profile not in ("baseline", "skill", "skill_subagent"):
        profile = DEFAULT_PROFILE
    if not run_lock.acquire(blocking=False):
        raise HTTPException(409, "агент уже выполняет задачу — подождите")
    files_now = list_xlsx()
    if files_now:
        task = ("Файлы .xlsx в рабочей папке: "
                + ", ".join(f"'{f['name']}'" for f in files_now)
                + ". Если в задаче файл не назван явно и он один — работай с ним.\n\n") + task

    def gen():
        try:
            from excel_agent.agent import stream_run
            for ev in stream_run(profile, WORKDIR, task):
                if ev.get("kind") == "final":
                    ev["files"] = list_xlsx()
                yield json.dumps(ev, ensure_ascii=False) + "\n"
        except Exception as e:  # noqa: BLE001
            yield json.dumps({"kind": "error", "text": f"{type(e).__name__}: {e}"[:400]}) + "\n"
        finally:
            run_lock.release()

    return StreamingResponse(gen(), media_type="application/x-ndjson")


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
  .log{display:none;flex-direction:column;gap:6px;max-height:340px;overflow:auto;margin-bottom:14px;
    background:rgba(8,12,24,.5);border:1px solid var(--line);border-radius:12px;padding:12px}
  .logline{display:flex;gap:9px;font-size:.88rem;line-height:1.45;padding:3px 2px;animation:fade .25s ease}
  .logline .li{flex-shrink:0}
  .logline .tx{color:var(--text);white-space:pre-wrap;word-break:break-word}
  .logline.tool .tx{color:#8fd0ff} .logline.result .tx{color:var(--muted)}
  .logline.thought .tx{color:#d7e0ff} .logline.info .tx{color:#ffd27d}
  .logline.error .tx{color:var(--danger)}
  @keyframes fade{from{opacity:0;transform:translateY(3px)}to{opacity:1}}
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
    <div class="step"><span class="num">🧩</span> Скиллы <span class="hint" style="font-weight:400">— необязательно</span></div>
    <div id="dropsk" class="drop">
      Перетащи <b>SKILL.md</b> или <b>нажми</b> — добавь свой навык агенту
      <input type="file" id="upsk" accept=".md,text/markdown" hidden>
    </div>
    <div id="skills" class="files"></div>
    <div class="hint" style="margin-top:8px">Встроенный скилл <b>excel</b> активен всегда. Загруженные добавляются к нему; в заголовке нужны поля <code>name</code> и <code>description</code>.</div>
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
    <div id="log" class="log"></div>
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
// --- skills ---
function renderSkills(ss){const el=document.getElementById('skills');
  let h='<div class="file"><span class="ic">🧩</span><span class="nm">excel<span class="sz">встроенный</span></span><span class="sz">всегда активен</span></div>';
  if(ss&&ss.length){h+=ss.map(s=>`<div class="file"><span class="ic">🧩</span>
    <span class="nm">${esc(s.name)}<span class="sz">${esc(s.description||'')}</span></span>
    <button class="dlbtn" style="background:rgba(255,107,107,.14);color:#ff9b9b;border-color:rgba(255,107,107,.4)" onclick="delSkill('${esc(s.name).replace(/'/g,"\\'")}')">✕ Удалить</button></div>`).join('')}
  el.innerHTML=h}
async function loadSkills(){try{const d=await j('api/skills');renderSkills(d.skills)}catch(e){}}
async function sendSkill(file){const fd=new FormData();fd.append('file',file);
  try{const d=await j('api/skill',{method:'POST',body:fd});renderSkills(d.skills);toast('Скилл добавлен: '+d.name)}
  catch(e){toast(e.message,true)}}
async function delSkill(name){const fd=new FormData();fd.append('name',name);
  try{const d=await j('api/skill/delete',{method:'POST',body:fd});renderSkills(d.skills);toast('Скилл удалён')}catch(e){toast(e.message,true)}}
const dropsk=document.getElementById('dropsk'),upsk=document.getElementById('upsk');
dropsk.onclick=()=>upsk.click();
upsk.onchange=()=>{if(upsk.files[0])sendSkill(upsk.files[0]);upsk.value=''};
['dragenter','dragover'].forEach(e=>dropsk.addEventListener(e,ev=>{ev.preventDefault();dropsk.classList.add('drag')}));
['dragleave','drop'].forEach(e=>dropsk.addEventListener(e,ev=>{ev.preventDefault();dropsk.classList.remove('drag')}));
dropsk.addEventListener('drop',ev=>{const f=ev.dataTransfer.files[0];if(f)sendSkill(f)});
// profile segmented control
document.querySelectorAll('#seg button').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('#seg button').forEach(x=>x.classList.remove('on'));
  b.classList.add('on');profile=b.dataset.v});
function addStep(kind,text){const log=document.getElementById('log');
  const ic={tool:'🔧',result:'📄',thought:'💭',info:'➡️',error:'⚠️'}[kind]||'•';
  const d=document.createElement('div');d.className='logline '+kind;
  d.innerHTML='<span class="li">'+ic+'</span><span class="tx">'+esc(text)+'</span>';
  log.appendChild(d);log.scrollTop=log.scrollHeight}
async function run(){const task=document.getElementById('task').value.trim();
  if(!task){toast('Впиши задачу',true);return}
  const go=document.getElementById('go');go.disabled=true;go.classList.add('busy');
  document.getElementById('gotext').textContent='Агент работает…';
  document.getElementById('status').textContent='шаги появляются вживую…';
  document.getElementById('out').style.display='block';
  const log=document.getElementById('log');log.style.display='flex';log.innerHTML='';
  document.getElementById('answer').innerHTML='';document.getElementById('badges').innerHTML='';
  const fd=new FormData();fd.append('task',task);fd.append('profile',profile);
  try{
    const r=await fetch('api/run_stream',{method:'POST',body:fd});
    if(!r.ok){let m='HTTP '+r.status;try{m=(await r.json()).detail||m}catch(e){}throw new Error(m)}
    const reader=r.body.getReader(),dec=new TextDecoder();let buf='',final=null;
    while(true){const {value,done}=await reader.read();if(done)break;
      buf+=dec.decode(value,{stream:true});let nl;
      while((nl=buf.indexOf('\n'))>=0){const line=buf.slice(0,nl).trim();buf=buf.slice(nl+1);
        if(!line)continue;let ev;try{ev=JSON.parse(line)}catch(e){continue}
        if(ev.kind==='final'){final=ev}
        else if(ev.kind==='error'){addStep('error',ev.text)}
        else{addStep(ev.kind,ev.text)}
      }}
    if(final){
      document.getElementById('answer').innerHTML='<div class="answer">'+esc(final.answer||'(пустой ответ)')+'</div>';
      document.getElementById('badges').innerHTML=
        '<span class="badge">🧩 профиль: '+profile+'</span>'+
        '<span class="badge">🔢 токены: '+(final.tokens??'?')+'</span>'+
        '<span class="badge">💬 шагов: '+(final.steps??'?')+'</span>';
      renderFiles(final.files);document.getElementById('status').textContent='готово';toast('Готово ✓');
    }else{document.getElementById('status').textContent='завершено';}
  }catch(e){addStep('error',e.message);
    document.getElementById('status').textContent='ошибка';toast(e.message,true)}
  go.disabled=false;go.classList.remove('busy');document.getElementById('gotext').textContent='Выполнить'}
refresh();loadSkills();
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
