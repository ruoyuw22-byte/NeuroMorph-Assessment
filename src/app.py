#!/usr/bin/env python3
# NeuroMorph Assessment (NMA) Lite 0.11.1 - local-only medical morphometry/report workflow for macOS
# Uses the user's existing MATLAB/SPM12/CAT12/dcm2niix/Python environment.

from __future__ import annotations

import argparse, csv, gzip, hashlib, io, json, math, os, re, shutil, signal, socket, subprocess, sys, tempfile, threading, time, traceback, urllib.parse, uuid, webbrowser, zipfile
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.etree import ElementTree as ET

APP_VERSION = "0.11.1"
RES_DIR = Path(__file__).resolve().parent
SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "BrainerLite"
CONFIG_PATH = SUPPORT_DIR / "config.json"
UPLOAD_DIR = SUPPORT_DIR / "uploads"
LOG_DIR = SUPPORT_DIR / "logs"
RUNTIME_ASSET_DIR = SUPPORT_DIR / "runtime_assets" / APP_VERSION
SOURCE_TEMPLATE = RES_DIR / "report_template.docx"
SOURCE_BACKGROUND = RES_DIR / "template_background.png"
SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
RUNTIME_ASSET_DIR.mkdir(parents=True, exist_ok=True)

# App Translocation can remove the temporary .app path while a background server
# is still alive. Copy report resources to persistent Application Support first.
def _persist_asset(src: Path, name: str):
    dst = RUNTIME_ASSET_DIR / name
    try:
        if src.is_file() and (not dst.is_file() or src.stat().st_size != dst.stat().st_size):
            shutil.copy2(src, dst)
    except Exception:
        pass
    return dst if dst.is_file() else src

BUNDLED_TEMPLATE = _persist_asset(SOURCE_TEMPLATE, "report_template.docx")
BUNDLED_BACKGROUND = _persist_asset(SOURCE_BACKGROUND, "template_background.png")

DEFAULT_CONFIG = {
    "matlab": "/Applications/MATLAB_R2022b.app/bin/matlab",
    "spm": str(Path.home() / "Documents" / "MATLAB" / "spm"),
    "cat12": str(Path.home() / "Documents" / "MATLAB" / "spm" / "toolbox" / "cat12"),
    "patient_root": str(Path.home() / "Desktop" / "AllSubjects"),
    "output_root": str(Path.home() / "Desktop" / "CAT12_batch_out"),
    "template": str(BUNDLED_TEMPLATE),
    "python": sys.executable,
    "dcm2niix": shutil.which("dcm2niix") or "/usr/local/bin/dcm2niix",
    "reuse_existing": True,
}

STATE = {
    "excel_rows": [],
    "excel_path": "",
    "jobs": {},
}
STATE_LOCK = threading.Lock()


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict): cfg.update(saved)
        except Exception: pass
    tmpl = str(cfg.get("template", "") or "")
    if (not tmpl) or ("AppTranslocation" in tmpl) or ("Brainer Lite.app/Contents/Resources/report_template.docx" in tmpl):
        cfg["template"] = DEFAULT_CONFIG["template"]
    if not str(cfg.get("python", "") or ""): cfg["python"] = DEFAULT_CONFIG["python"]
    if not Path(str(cfg.get("dcm2niix", ""))).expanduser().exists():
        found=shutil.which("dcm2niix")
        if found: cfg["dcm2niix"]=found
    return cfg


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def path_exists(p, kind="any"):
    try:
        q = Path(os.path.expanduser(str(p))).resolve()
        if kind == "file": return q.is_file()
        if kind == "dir": return q.is_dir()
        return q.exists()
    except Exception:
        return False



def _dir_readable(path):
    p=Path(str(path or "")).expanduser()
    if not p.is_dir(): return False
    try:
        with os.scandir(p) as it:
            next(it, None)
        return True
    except Exception:
        return False

def _probe_python(python_exe):
    out={"exists":False,"version":"","executable":"","core":{},"excel":{},"catmat":False}
    p=Path(str(python_exe or "")).expanduser()
    if not p.is_file(): return out
    out["exists"]=True
    script="""import importlib,json,sys
mods=['numpy','nibabel','matplotlib','PIL','pandas','openpyxl','h5py']
r={'version':sys.version.split()[0],'executable':sys.executable,'mods':{}}
for m in mods:
    try:
        x=importlib.import_module(m);r['mods'][m]={'ok':True,'version':str(getattr(x,'__version__',''))}
    except Exception as e:r['mods'][m]={'ok':False,'error':str(e)}
print(json.dumps(r,ensure_ascii=False))"""
    try:
        cp=subprocess.run([str(p),"-c",script],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=25)
        if cp.returncode==0 and cp.stdout.strip():
            j=json.loads(cp.stdout.strip().splitlines()[-1]);mods=j.get("mods",{})
            out["version"]=j.get("version","");out["executable"]=j.get("executable","")
            out["core"]={k:bool(mods.get(k,{}).get("ok")) for k in ["numpy","nibabel","matplotlib"]}
            out["report"]={"PIL":bool(mods.get("PIL",{}).get("ok"))}
            out["excel"]={k:bool(mods.get(k,{}).get("ok")) for k in ["pandas","openpyxl"]}
            out["catmat"]=bool(mods.get("h5py",{}).get("ok"));out["details"]=mods
        else: out["error"]=(cp.stderr or cp.stdout or "Python 检测失败").strip()[-1200:]
    except Exception as e: out["error"]=str(e)
    return out

def dependency_status(cfg=None):
    cfg=cfg or load_config(); py=_probe_python(cfg.get("python"))
    tmpl=Path(str(cfg.get("template") or "")).expanduser()
    out_root=Path(str(cfg.get("output_root") or "")).expanduser()
    items={
      "MATLAB":path_exists(cfg.get("matlab"),"file"),"SPM12":_dir_readable(cfg.get("spm")),"CAT12":_dir_readable(cfg.get("cat12")),
      "Python解释器":bool(py.get("exists")),
      "Python影像依赖":bool(py.get("core")) and all(py.get("core",{}).values()),
      "报告绘图组件":bool(py.get("report",{}).get("PIL")),
      "dcm2niix":path_exists(cfg.get("dcm2niix"),"file"),"患者数据目录":path_exists(cfg.get("patient_root"),"dir"),
      "报告模板":tmpl.is_file() or BUNDLED_TEMPLATE.is_file()}
    # MATLAB/CAT12 are not mandatory for patients whose imaging is unrecorded or already analyzed.
    basic_names=["Python解释器","报告绘图组件","患者数据目录","报告模板"]
    return {"items":items,"python":py,"excel_components":bool(py.get("excel")) and all(py.get("excel",{}).values()),
            "catmat_component":bool(py.get("catmat")),
            "output_root":{"path":str(out_root),"exists":out_root.is_dir()},
            "ready":all(items.get(k,False) for k in basic_names),"running_python":sys.executable,"app_version":APP_VERSION}


# ------------------------------ XLSX reader (stdlib) ------------------------------

def _col_index(ref):
    m = re.match(r"([A-Z]+)", ref or "")
    if not m: return 0
    n = 0
    for ch in m.group(1): n = n * 26 + (ord(ch) - 64)
    return n - 1


def read_xlsx_first_sheet(path: Path):
    """Minimal XLSX reader sufficient for the supplied patient workbook."""
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("a:si", ns):
                shared.append("".join((t.text or "") for t in si.findall(".//a:t", ns)))
        wbroot = ET.fromstring(z.read("xl/workbook.xml"))
        first = wbroot.find("a:sheets/a:sheet", ns)
        rid = first.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        relroot = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        target = None
        for rel in relroot:
            if rel.attrib.get("Id") == rid:
                target = rel.attrib.get("Target"); break
        if not target: raise RuntimeError("无法定位 Excel 第一张工作表")
        sheet_name = "xl/" + target.lstrip("/") if not target.startswith("xl/") else target
        sroot = ET.fromstring(z.read(sheet_name))
        rows = []
        for row in sroot.findall(".//a:sheetData/a:row", ns):
            vals = {}
            for c in row.findall("a:c", ns):
                idx = _col_index(c.attrib.get("r", ""))
                typ = c.attrib.get("t", "")
                v = c.find("a:v", ns)
                inline = c.find("a:is", ns)
                val = None
                if typ == "s" and v is not None and v.text is not None:
                    try: val = shared[int(v.text)]
                    except Exception: val = v.text
                elif typ == "inlineStr" and inline is not None:
                    val = "".join((t.text or "") for t in inline.findall(".//a:t", ns))
                elif v is not None and v.text is not None:
                    raw = v.text
                    if typ == "b": val = raw == "1"
                    elif typ in ("str", "e"): val = raw
                    else:
                        try:
                            f = float(raw); val = int(f) if f.is_integer() else f
                        except Exception: val = raw
                vals[idx] = val
            if vals:
                maxidx = max(vals)
                rows.append([vals.get(i) for i in range(maxidx + 1)])
        if not rows: return []
        headers = [str(x).strip() if x is not None else "" for x in rows[0]]
        out = []
        for arr in rows[1:]:
            rec = {}
            for i,h in enumerate(headers):
                if h: rec[h] = arr[i] if i < len(arr) else None
            if any(v not in (None, "") for v in rec.values()): out.append(rec)
        return out


def normalize_patient_row(rec):
    def pick(*names):
        for n in names:
            if n in rec and rec[n] not in (None, ""):
                return rec[n]
        return None
    case_id = pick("字段拼接", "病例编号", "病例ID", "ID", "Subject", "subject")
    return {
        "group": pick("组别", "Group"),
        "case_id": str(case_id).strip() if case_id is not None else "",
        "name": pick("姓名", "Name"),
        "sex": pick("性别", "Sex"),
        "age": pick("年龄", "Age"),
        "weight": pick("体重", "Weight"),
        "MMSE": pick("MMSE"),
        "MCCA": pick("MCCA", "MoCA", "MOCA"),
        "HAMD": pick("HAMD"),
        "HAMA": pick("HAMA"),
    }


def safe_num(x):
    if x in (None, ""): return None
    try: return float(x)
    except Exception: return None


def fmt_score(x):
    x = safe_num(x)
    if x is None: return ""
    return str(int(x)) if float(x).is_integer() else f"{x:g}"


def interpret_scales(p):
    def mmse(v):
        v = safe_num(v)
        if v is None: return "未填写"
        if v < 0: return "无效"
        if v < 17: return "认知障碍"
        if v <= 20: return "认知损伤"
        return "正常"
    def mcca(v):
        v = safe_num(v)
        if v is None: return "未填写"
        if v < 0: return "无效"
        if v < 10: return "认知障碍"
        if v <= 23: return "认知损伤"
        return "正常"
    def hama(v):
        v = safe_num(v)
        if v is None: return "未填写"
        if v < 0: return "无效"
        if v <= 7: return "正常"
        if v <= 13: return "焦虑情绪"
        return "焦虑"
    def hamd(v):
        v = safe_num(v)
        if v is None: return "未填写"
        if v < 0: return "无效"
        if v <= 8: return "正常"
        if v <= 19: return "抑郁情绪"
        return "抑郁"
    return {"MCCA": mcca(p.get("MCCA")), "MMSE": mmse(p.get("MMSE")),
            "HAMA": hama(p.get("HAMA")), "HAMD": hamd(p.get("HAMD"))}


# ------------------------------ CAT12 workflow ------------------------------
POS_MUST = [r"\bt1\b", r"mprage", r"mp2rage", r"\bspgr\b", r"\btfl\b"]
NEG_HARD = ["t2", "flair", "fse", "propeller", "bold", "epi", "dwi", "diff", "dti",
            "scout", "ahead", "localizer", "loc", "survey", "tof", "angio", "field", "map"]


def get_imaging_libs():
    import numpy as np
    import nibabel as nib
    return np, nib


def voxel_volume_mm3(img):
    np, _ = get_imaging_libs()
    return float(abs(np.linalg.det(img.affine[:3,:3])))


def pick_t1(nifti_dir: Path):
    np, nib = get_imaging_libs()
    all_niis = sorted(nifti_dir.glob("*.nii*"))
    cand = []
    for p in all_niis:
        n = p.name.lower()
        if any(k in n for k in NEG_HARD): continue
        if not any(re.search(pat, n) for pat in POS_MUST): continue
        try:
            img = nib.load(str(p))
            z = img.shape[2] if len(img.shape) == 3 else -1
            vox = voxel_volume_mm3(img)
            s = 0
            if len(img.shape) == 3 and z >= 160: s += 10
            if vox < 1.2: s += 5
            if "iso" in n: s += 1
            if "sag" in n: s += 1
            cand.append((s, z, vox, p))
        except Exception: pass
    if not cand: raise RuntimeError("未找到合格 T1：命名或影像维度不符合既有规则。")
    s,z,vox,p = sorted(cand, key=lambda x:(x[0],x[1],-x[2],x[3].name), reverse=True)[0]
    if s < 12 or z < 160 or vox > 1.5:
        raise RuntimeError(f"T1 候选未通过硬阈值：score={s}, z={z}, voxel={vox:.3f} mm³")
    return p


def run_cmd(cmd, job, cwd=None):
    cmd=[str(x) for x in cmd]
    job_log(job, "[RUN] " + " ".join(cmd))
    env = os.environ.copy()
    env["PATH"] = "/usr/local/bin:/opt/homebrew/bin:/opt/anaconda3/bin:" + str(Path.home()/"anaconda3/bin") + ":" + env.get("PATH", "")
    tail=[]
    try:
        p = subprocess.Popen(cmd, cwd=str(cwd) if cwd else None, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    except Exception as e:
        raise RuntimeError(f"无法启动外部命令：{cmd[0]}\n{e}")
    if p.stdout:
        for line in p.stdout:
            line=line.rstrip("\r\n")
            if line:
                tail.append(line)
                if len(tail)>60: tail=tail[-60:]
                job_log(job,line)
    code=p.wait()
    if code != 0:
        details="\n".join(tail[-24:]).strip()
        msg=f"命令执行失败（退出码 {code}）：{' '.join(cmd)}"
        if details: msg += "\n\n工具输出末尾：\n"+details
        else: msg += "\n\n该工具没有返回可见输出。请在运行日志中检查命令与目录权限。"
        raise RuntimeError(msg)
    return tail


def write_mat_batch(mat_path: Path, t1_path: Path, spm_dir: str, cat_dir: str):
    t1p = str(t1_path).replace("\\", "/").replace("'", "''")
    spm = str(Path(spm_dir).resolve()).replace("\\", "/").replace("'", "''")
    cat = str(Path(cat_dir).resolve()).replace("\\", "/").replace("'", "''")
    code = f"""
addpath('{spm}');
addpath('{cat}');
spm('defaults','fmri'); spm_jobman('initcfg');
matlabbatch = {{}};
matlabbatch{{1}}.spm.tools.cat.estwrite.data = {{ '{t1p},1' }};
matlabbatch{{1}}.spm.tools.cat.estwrite.nproc = 0;
matlabbatch{{1}}.spm.tools.cat.estwrite.opts.tpm = {{fullfile('{spm}','tpm','TPM.nii')}};
matlabbatch{{1}}.spm.tools.cat.estwrite.opts.affreg = 'mni';
matlabbatch{{1}}.spm.tools.cat.estwrite.extopts.APP = 1;
matlabbatch{{1}}.spm.tools.cat.estwrite.extopts.REG.strength = 0.5;
matlabbatch{{1}}.spm.tools.cat.estwrite.extopts.gcutstr = 0.5;
matlabbatch{{1}}.spm.tools.cat.estwrite.extopts.spm_kamap = 1;
matlabbatch{{1}}.spm.tools.cat.estwrite.output.surface = 1;
matlabbatch{{1}}.spm.tools.cat.estwrite.output.surf_meas = 1;
matlabbatch{{1}}.spm.tools.cat.estwrite.output.ROImenu.atlases = 1;
matlabbatch{{1}}.spm.tools.cat.estwrite.output.GM.native  = 1;
matlabbatch{{1}}.spm.tools.cat.estwrite.output.WM.native  = 1;
matlabbatch{{1}}.spm.tools.cat.estwrite.output.CSF.native = 1;
spm_jobman('run',matlabbatch);
"""
    mat_path.write_text(code, encoding="utf-8")


def matlab_spm_preflight(cfg, job):
    """Verify MATLAB launched by NMA can actually read SPM/CAT12, not merely that Python can see them."""
    spm=str(Path(cfg["spm"]).expanduser()).replace("'","''")
    cat=str(Path(cfg["cat12"]).expanduser()).replace("'","''")
    cmd=("try, "
         f"assert(exist('{spm}','dir')==7,'NMA_SPM_DIR_DENIED'); "
         f"assert(exist('{cat}','dir')==7,'NMA_CAT12_DIR_DENIED'); "
         f"addpath('{spm}'); addpath('{cat}'); "
         "assert(exist('spm','file')==2,'NMA_SPM_NOT_ON_PATH'); "
         "disp(['NMA_SPM_OK:' which('spm')]); "
         "catch e, disp(getReport(e,'extended','hyperlinks','off')); exit(73); end; exit(0);")
    try:
        run_cmd([cfg["matlab"],"-batch",cmd],job)
    except RuntimeError as e:
        t=str(e)
        if any(k in t for k in ['目录访问失败','NMA_SPM_DIR_DENIED','NMA_CAT12_DIR_DENIED','NMA_SPM_NOT_ON_PATH','Operation not permitted']):
            raise RuntimeError("MATLAB 无法读取 SPM12/CAT12 目录。请打开‘高级设置’，分别用 SPM12 和 CAT12 右侧的‘选择/授权’重新选择目录并保存；若 macOS 仍拒绝，请在 系统偏好设置 → 安全性与隐私 → 隐私 → 文件与文件夹/完全磁盘访问权限 中允许 NeuroMorph Assessment 和 MATLAB 访问‘文稿’目录。") from e
        raise


def run_cat12(nifti_dir: Path, t1_nii: Path, cfg, job):
    matlab_spm_preflight(cfg,job)
    work = nifti_dir
    mat_work = Path(cfg.get("output_root") or (Path(cfg["patient_root"]).resolve().parent / "CAT12_batch_out")).expanduser().resolve() / "mat_work"
    mat_work.mkdir(parents=True, exist_ok=True)
    mat_file = mat_work / f"run_cat12_{job['case_id']}.m"
    write_mat_batch(mat_file, t1_nii, cfg["spm"], cfg["cat12"])
    logf = work / "matlab_cat12.log"
    mf = str(mat_file.resolve()).replace("'", "''")
    lf = str(logf.resolve()).replace("'", "''")
    matlab_cmd = (f"try, diary('{lf}'); diary on; run('{mf}'); diary off; "
                  f"catch e, diary off; disp(getReport(e,'extended','hyperlinks','off')); exit(1); end; exit(0);")
    run_cmd([cfg["matlab"], "-batch", matlab_cmd], job, cwd=work)
    ok = list((work/"report").glob("catreport_*.pdf")) or list((work/"report").glob("cat_*.xml")) or list((work/"mri").glob("cat_*.mat"))
    if not ok: raise RuntimeError("CAT12 未生成预期 report/mri 文件，请查看 matlab_cat12.log。")


def _normalize_cm3(v):
    if v is None: return None
    try:
        import numpy as np
        a=np.asarray(v).squeeze()
        if getattr(a,"size",1) != 1: return None
        x=float(a)
    except Exception:
        try: x=float(v)
        except Exception: return None
    # CAT outputs encountered in practice can be either mm^3 or cm^3/ml.
    return x/1000.0 if abs(x) > 10000 else x


def _reconcile_tiv(csf, gm, wm, tiv=None):
    """Return TIV in cm³, cross-checking it against tissue absolute volumes.

    CAT12 outputs encountered across versions may expose more than one TIV-like field
    or use a different scale for a stored scalar.  CSF + GM + WM is the invariant
    absolute-volume definition used by this application, so an implausible/inconsistent
    direct TIV value must never be allowed to replace the tissue-volume sum.
    """
    try:
        tissues = [float(csf), float(gm), float(wm)]
        if not all(math.isfinite(v) and v >= 0 for v in tissues):
            tissues = None
    except Exception:
        tissues = None

    tissue_total = sum(tissues) if tissues is not None else None

    direct = _normalize_cm3(tiv) if tiv is not None else None
    try:
        if direct is not None:
            direct = float(direct)
            if not math.isfinite(direct) or direct <= 0:
                direct = None
    except Exception:
        direct = None

    if tissue_total is None:
        return direct
    if direct is None:
        return tissue_total

    # Try common unit/scale variants and keep a direct CAT12 value only when it
    # agrees with the absolute tissue volumes.  This catches values such as 1.x
    # being mistaken for ~1,200–1,500 cm³ while preserving valid CAT12 TIV.
    candidates = [direct]
    if direct < 20:
        candidates.append(direct * 1000.0)
    if direct > 10000:
        candidates.append(direct / 1000.0)
    best = min(candidates, key=lambda x: abs(x - tissue_total))
    tolerance = max(25.0, 0.05 * tissue_total)
    if abs(best - tissue_total) <= tolerance:
        return best

    # When the direct field is internally inconsistent, use the mathematically
    # coherent CAT12 absolute tissue sum.
    return tissue_total


def _h5_try_get(h5, keys):
    for k in keys:
        try:
            if k in h5:
                return h5[k][()]
        except Exception: pass
        try:
            obj=h5
            ok=True
            for seg in k.strip("/").split("/"):
                if seg in obj: obj=obj[seg]
                else: ok=False; break
            if ok: return obj[()]
        except Exception: pass
    return None


def extract_catmat_volumes(nifti_dir: Path):
    mats=sorted((nifti_dir/"mri").glob("cat_*.mat"))
    if not mats: return None
    try: import h5py
    except Exception: return None
    for mp in mats:
        try:
            with h5py.File(str(mp),"r") as f:
                csf=_normalize_cm3(_h5_try_get(f,["/CAT/Measures/vol_CSF","/cat/vol/CSF","/S/vol/CSF"]))
                gm =_normalize_cm3(_h5_try_get(f,["/CAT/Measures/vol_GM", "/cat/vol/GM", "/S/vol/GM"]))
                wm =_normalize_cm3(_h5_try_get(f,["/CAT/Measures/vol_WM", "/cat/vol/WM", "/S/vol/WM"]))
                tiv_raw=_h5_try_get(f,["/CAT/Measures/TIV", "/cat/TIV", "/S/TIV"])
                tiv=_reconcile_tiv(csf,gm,wm,tiv_raw)
                if None not in (gm,wm,tiv): return {"CSF":csf,"GM":gm,"WM":wm,"TIV":tiv,"source":str(mp)}
        except Exception: pass
    return None


def _summary_has_case(output_root: Path, case_id: str):
    p=output_root/"CAT12_volumes_summary.csv"
    if not p.is_file(): return False
    key=_case_key(case_id)
    try:
        with p.open("r",encoding="utf-8-sig",newline="") as f:
            return any(_case_key(str(r.get("Subject","")).strip())==key for r in csv.DictReader(f))
    except Exception: return False


def _scan_names(folder: Path):
    """Enumerate a folder without pathlib.glob silently looking empty on protected macOS folders."""
    if not folder.is_dir(): return []
    try:
        return [ent.name for ent in os.scandir(folder) if ent.is_file(follow_symlinks=False)]
    except PermissionError:
        raise
    except OSError:
        raise


def cat_reuse_evidence(nifti_dir: Path, output_root: Path, case_id: str):
    """Return concrete evidence that CAT12 has already produced extractable results.

    This deliberately mirrors the real CAT12 layout used by this project:
      _dcm2niix_out/report/cat_*.xml
      _dcm2niix_out/report/cat_*.mat
      _dcm2niix_out/report/catreport_*.pdf
      _dcm2niix_out/mri/p0*, p1*, p2*
      CAT12_volumes_summary.csv
    A report PDF by itself is logged as evidence but is not considered sufficient unless
    an extractable XML/MAT/summary/probability-map source is also present.
    """
    ev=[]
    if not nifti_dir.is_dir(): return ev
    report=nifti_dir/"report"; mri=nifti_dir/"mri"
    report_names=_scan_names(report) if report.is_dir() else []
    mri_names=_scan_names(mri) if mri.is_dir() else []
    for name in report_names:
        low=name.lower()
        if low.startswith("cat_") and low.endswith(".xml"):
            ev.append(str(report/name))
        elif low.startswith("cat_") and low.endswith(".mat"):
            ev.append(str(report/name))
        elif low.startswith("catreport_") and low.endswith(".pdf"):
            ev.append(str(report/name)+" [report]")
    for name in mri_names:
        low=name.lower()
        if low.startswith("cat_") and low.endswith(".mat"):
            ev.append(str(mri/name))
    if _summary_has_case(output_root,case_id):
        ev.append(str(output_root/"CAT12_volumes_summary.csv")+" [summary]")
    def has_prefix(prefix):
        return any(n.lower().startswith(prefix) and (n.lower().endswith('.nii') or n.lower().endswith('.nii.gz')) for n in mri_names)
    if has_prefix('p0') and has_prefix('p1') and has_prefix('p2'):
        ev.append(str(mri)+" [p0/p1/p2]")
    return ev


def has_reusable_cat(nifti_dir: Path, output_root: Path, case_id: str):
    ev=cat_reuse_evidence(nifti_dir,output_root,case_id)
    # Require at least one extractable source, not merely catreport_*.pdf.
    return any(not x.endswith(" [report]") for x in ev)

def extract_xml_volumes(nifti_dir: Path):
    xmls = sorted((nifti_dir / "report").glob("cat_*.xml"))
    for xp in xmls:
        try:
            root = ET.parse(xp).getroot()
            vals = None; tiv = None
            for elem in root.iter():
                tag = elem.tag.split("}")[-1]
                txt = (elem.text or "").strip()
                if tag == "vol_abs_CGW" and txt:
                    nums = [float(x) for x in re.findall(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", txt)]
                    if len(nums) >= 3: vals = nums
                elif tag == "vol_TIV" and txt:
                    nums = [float(x) for x in re.findall(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", txt)]
                    if nums: tiv = nums[0]
            if vals:
                csf, gm = vals[0], vals[1]
                wm = vals[2] + (vals[3] if len(vals) > 3 else 0.0)  # report WM includes WMHs
                tiv = _reconcile_tiv(csf, gm, wm, tiv)
                return {"CSF": csf, "GM": gm, "WM": wm, "TIV": tiv, "source": str(xp)}
        except Exception: pass
    return None


def extract_summary_csv(output_root: Path, case_id: str):
    p = output_root / "CAT12_volumes_summary.csv"
    if not p.exists(): return None
    try:
        with p.open("r", encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                if str(r.get("Subject", "")).strip() != case_id: continue
                def cv(*ks):
                    for k in ks:
                        if r.get(k) not in (None, ""):
                            try:
                                v=float(r[k]); return v/1000 if v > 10000 else v
                            except Exception: pass
                    return None
                out={"CSF":cv("CSF","CSF_mm3"),"GM":cv("GM","GM_mm3"),"WM":cv("WM","WM_mm3"),"TIV":cv("TIV","TIV_mm3"),"source":str(p)}
                if None not in (out.get("CSF"), out.get("GM"), out.get("WM")):
                    out["TIV"] = _reconcile_tiv(out["CSF"], out["GM"], out["WM"], out.get("TIV"))
                if all(out[k] is not None for k in ["GM","WM","TIV"]): return out
    except Exception: pass
    return None


def extract_probability_volumes(nifti_dir: Path):
    np, nib = get_imaging_libs()
    mri = nifti_dir / "mri"
    def first(pat):
        a=sorted(mri.glob(pat)); return a[0] if a else None
    csf,gm,wm = first("p0*.nii*"), first("p1*.nii*"), first("p2*.nii*")
    if not (csf and gm and wm): return None
    def volume(p):
        img=nib.load(str(p)); return float(img.get_fdata().sum()*voxel_volume_mm3(img)/1000.0)
    c,g,w=volume(csf),volume(gm),volume(wm)
    return {"CSF":c,"GM":g,"WM":w,"TIV":c+g+w,"source":"probability maps"}


def get_volumes(nifti_dir, output_root, case_id):
    # Keep the validated notebook's priority: cat_*.mat first, then XML/CSV/probability maps.
    funcs=(lambda: extract_catmat_volumes(nifti_dir), lambda: extract_xml_volumes(nifti_dir),
           lambda: extract_summary_csv(output_root, case_id), lambda: extract_probability_volumes(nifti_dir))
    for fn in funcs:
        try: r=fn()
        except Exception: r=None
        if r and all(r.get(k) is not None for k in ["GM","WM","TIV"]): return r
    raise RuntimeError("未能从 CAT12 cat_*.mat、XML、汇总 CSV 或概率图中提取 TIV/GM/WM。")


# ------------------------------ 3-view MRI figure ------------------------------

def build_3view_figure_nice(nii_path: Path):
    import numpy as np
    import nibabel as nib
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import gridspec

    nii_path=Path(nii_path)
    if nii_path.suffixes[-2:] == [".nii", ".gz"]:
        json_path=nii_path.with_suffix("").with_suffix(".json")
    else: json_path=nii_path.with_suffix(".json")
    meta={}
    if json_path.exists():
        try: meta=json.loads(json_path.read_text(encoding="utf-8"))
        except Exception: pass
    get=lambda k,d="": meta.get(k,d) if meta else d
    name=str(get("PatientName","")).replace("^"," ")
    pid=str(get("PatientID", "")); sex=str(get("PatientSex", "")); bdate=str(get("PatientBirthDate", ""))
    birth_str=""
    if bdate:
        try: birth_str=datetime.strptime(bdate,"%Y%m%d").strftime("%d-%B-%Y")
        except Exception: birth_str=bdate
    inst=str(get("InstitutionName", "")); station=str(get("StationName", ""))
    series_desc=str(get("SeriesDescription", get("ProtocolName", "")))
    slice_thk=get("SliceThickness", ""); field_strength=get("MagneticFieldStrength", "")
    modality=str(get("Modality", "MR")); transfer=str(get("TransferSyntaxUID", "LittleEndianExplicit")); series_no=get("SeriesNumber", "")
    slice_loc=get("SliceLocation", "")
    img=nib.load(str(nii_path)); data=img.get_fdata(); sx,sy,sz=data.shape
    p1,p99=np.percentile(data,(1,99))
    if p99>p1: data=np.clip((data-p1)/(p99-p1),0,1)
    sag=data[sx//2,:,:]; cor=data[:,sy//2,:]; ax=data[:,:,sz//2]
    if slice_loc in ("",None):
        try:
            world=nib.affines.apply_affine(img.affine,np.array([sx/2,sy/2,sz//2])); slice_loc=float(world[2])
        except Exception: slice_loc=""
    M=max(*sag.shape,*ax.shape,*cor.shape)
    def pad(arr):
        h,w=arr.shape; out=np.zeros((M,M),dtype=arr.dtype); py=(M-h)//2; px=(M-w)//2; out[py:py+h,px:px+w]=arr; return out
    views=[pad(sag),pad(ax),pad(cor)]; titles=["Sagittal","Axial","Coronal"]
    fs_str=f"{field_strength:.2f}" if isinstance(field_strength,(int,float)) else str(field_strength)
    sl_str=f"{slice_loc:.2f}" if isinstance(slice_loc,(int,float)) else str(slice_loc or "")
    top=[]
    for s in [name,pid,(birth_str+"  "+sex).strip(),(inst+"  "+station).strip(),series_desc]:
        if s: top.append(s)
    bottom=[]; stsl=""
    if slice_thk != "": stsl+=f"ST: {slice_thk} mm"
    if sl_str: stsl+=("   " if stsl else "")+f"SL: {sl_str} mm"
    if stsl: bottom.append(stsl)
    if fs_str: bottom.append(f"FS: {fs_str}")
    if modality: bottom.append(modality)
    if transfer: bottom.append(transfer)
    bottom.append(f"Images: {sz//2+1}/{sz}")
    if series_no != "": bottom.append(f"Series: {series_no}")
    fig=plt.figure(figsize=(10,3.5),facecolor="black")
    gs=gridspec.GridSpec(1,4,width_ratios=[1.3,1,1,1],wspace=0.03)
    at=fig.add_subplot(gs[0]); at.axis("off"); y=.95
    for line in top: at.text(0,y,line,color="white",fontsize=10,fontfamily="monospace"); y-=.085
    y=.55
    for line in bottom: at.text(0,y,line,color="white",fontsize=10,fontfamily="monospace"); y-=.085
    for i,(v,t) in enumerate(zip(views,titles),1):
        a=fig.add_subplot(gs[i]); a.imshow(np.rot90(v),cmap="gray"); a.axis("off"); a.set_title(t,fontsize=12,color="white",pad=6)
    plt.tight_layout(rect=[.01,.01,.99,.99])
    out=nii_path.with_name(nii_path.stem+"_3views_nice.png")
    fig.savefig(out,dpi=300,bbox_inches="tight",facecolor=fig.get_facecolor()); plt.close(fig)
    return out


# ------------------------------ Report compositor ------------------------------

def find_font(size):
    from PIL import ImageFont
    candidates=[
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/STHeiti Medium.ttc",
    ]
    for p in candidates:
        if Path(p).exists():
            try: return ImageFont.truetype(p,size=size,index=0)
            except Exception: pass
    return ImageFont.load_default()


def resolve_template_background(cfg, job=None):
    """Return a stable report background without qlmanage or AppTranslocation dependencies.

    The current Lite product supports the supplied report layout. A user-selected DOCX is
    accepted as the configured template source, but the page layout is rendered from the
    bundled, build-time snapshot so report generation is deterministic and fully offline.
    """
    fallback = BUNDLED_BACKGROUND
    if not fallback.is_file():
        raise RuntimeError("内置报告模板背景缺失。请重新解压当前版本 NMA。")
    tmpl = Path(str(cfg.get("template") or "")).expanduser()
    if tmpl.suffix.lower() in (".png", ".jpg", ".jpeg") and tmpl.is_file():
        if job: job_log(job, "使用自定义图像模板：" + str(tmpl))
        return tmpl
    if tmpl.is_file():
        if job: job_log(job, "报告模板：" + str(tmpl) + "；使用内置同版式背景生成 PDF。")
    else:
        if job: job_log(job, "配置的 DOCX 模板不存在；使用 NMA 内置同版式模板。")
    return fallback

def compose_report(patient, interp, volumes, brain_png, output_pdf: Path, background_path=None, imaging_note=""):
    from PIL import Image, ImageDraw
    base=Image.open(background_path or BUNDLED_BACKGROUND).convert("RGB")
    W,H=base.size; d=ImageDraw.Draw(base)
    f=find_font(max(24,int(W*.019))); fsmall=find_font(max(22,int(W*.017))); fnote=find_font(max(24,int(W*.020)))
    d.text((W*.340,H*.188),str(patient.get("name") or ""),font=f,fill="black",anchor="mm")
    age=fmt_score(patient.get("age")); d.text((W*.850,H*.188),(age+" 岁") if age else "",font=f,fill="black",anchor="mm")
    d.text((W*.340,H*.229),str(patient.get("sex") or ""),font=f,fill="black",anchor="mm")
    wt=fmt_score(patient.get("weight")); d.text((W*.850,H*.229),(wt+" KG") if wt else "",font=f,fill="black",anchor="mm")
    ys=[.368,.406,.443,.480]
    for key,y in zip(["MCCA","MMSE","HAMA","HAMD"],ys):
        d.text((W*.413,H*y),fmt_score(patient.get(key)),font=fsmall,fill="black",anchor="mm")
        d.text((W*.941,H*y),interp.get(key,""),font=fsmall,fill="black",anchor="mm")
    # MRI panel or explicit unrecorded state
    has_brain=False
    try: has_brain=bool(brain_png) and Path(brain_png).exists()
    except Exception: has_brain=False
    if has_brain:
        im=Image.open(brain_png).convert("RGB")
        target=(int(W*.040),int(H*.568),int(W*.960),int(H*.793)); tw,th=target[2]-target[0],target[3]-target[1]
        im.thumbnail((tw,th),Image.Resampling.LANCZOS); x=target[0]+(tw-im.width)//2; y=target[1]+(th-im.height)//2
        base.paste(im,(x,y))
    else:
        d.text((W*.500,H*.675), imaging_note or "核磁影像未录入", font=fnote, fill=(115,125,135), anchor="mm")
    if volumes and all(volumes.get(k) is not None for k in ["TIV","GM","WM"]):
        tiv=int(round(float(volumes["TIV"]))); gm=int(round(float(volumes["GM"]))); wm=int(round(float(volumes["WM"])))
        d.text((W*.120,H*.862),f"{tiv}（cm^3）",font=fsmall,fill="black",anchor="lm")
        d.text((W*.340,H*.862),f"{gm}（cm^3）",font=fsmall,fill="black",anchor="lm")
        d.text((W*.560,H*.862),f"{wm}（cm^3）",font=fsmall,fill="black",anchor="lm")
    else:
        for x in [W*.120,W*.340,W*.560]: d.text((x,H*.862),"未录入",font=fsmall,fill=(90,100,110),anchor="lm")
    now=datetime.now()
    d.text((W*.800,H*.950),str(now.year),font=fsmall,fill="black",anchor="mm")
    d.text((W*.842,H*.950),str(now.month),font=fsmall,fill="black",anchor="mm")
    d.text((W*.880,H*.950),str(now.day),font=fsmall,fill="black",anchor="mm")
    output_pdf.parent.mkdir(parents=True,exist_ok=True)
    base.save(output_pdf,"PDF",resolution=150.0,quality=95)
    preview=output_pdf.with_suffix(".png"); base.save(preview,"PNG")
    return output_pdf, preview


# ------------------------------ Job workflow ------------------------------

def job_log(job, msg):
    ts=datetime.now().strftime("%H:%M:%S")
    with STATE_LOCK:
        job.setdefault("logs",[]).append(f"[{ts}] {msg}")
        if len(job["logs"])>500: job["logs"]=job["logs"][-500:]
    try:
        (LOG_DIR/f"{job['id']}.log").write_text("\n".join(job.get("logs",[])),encoding="utf-8")
    except Exception: pass


def set_stage(job, stage, progress):
    with STATE_LOCK: job["stage"],job["progress"]=stage,progress
    job_log(job, stage)


def normalize_patient_data(data):
    p={k:data.get(k) for k in ["case_id","name","sex","age","weight","MMSE","MCCA","HAMA","HAMD","group"]}
    p["case_id"]=str(p.get("case_id") or "").strip()
    return p


STEP_KEYS=["env","patient","imaging","cat12","volume","brain","report"]

def _init_steps(job):
    job["steps"]={k:{"state":"pending"} for k in STEP_KEYS}

def set_step(job,key,state,note=""):
    with STATE_LOCK:
        job.setdefault("steps",{}).setdefault(key,{})["state"]=state
        if note: job["steps"][key]["note"]=note


def output_root_for(cfg):
    raw = str(cfg.get("output_root") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(cfg["patient_root"]).expanduser().resolve().parent / "CAT12_batch_out"


def candidate_output_roots(cfg):
    """Candidate CAT12 roots, ordered by user intent then sensible project locations."""
    roots=[]
    def add(x):
        if not x: return
        try: q=Path(str(x)).expanduser().resolve()
        except Exception: return
        if q not in roots: roots.append(q)
    add(cfg.get("output_root"))
    try: add(Path(str(cfg.get("template") or "")).expanduser().resolve().parent / "CAT12_batch_out")
    except Exception: pass
    try: add(Path(str(cfg.get("patient_root") or "")).expanduser().resolve().parent / "CAT12_batch_out")
    except Exception: pass
    add(Path.home() / "Desktop" / "CAT12_batch_out")
    return roots


def _case_key(value):
    """Normalize case identifiers for robust folder matching without changing display text.
    Examples: 032_RXS, 032-RXS, 32_RXS -> 32RXS.
    """
    import re
    x=str(value or "").strip().upper().replace("＿","_").replace("－","-")
    x=re.sub(r"[\s_\-]+","",x)
    m=re.match(r"^(\d+)(.*)$",x)
    if m:
        try: x=str(int(m.group(1)))+m.group(2)
        except Exception: pass
    return x


def _match_case_dir(root: Path, case_id: str):
    """Return the best matching patient/case directory under root."""
    try: root=root.expanduser().resolve()
    except Exception: root=Path(root).expanduser()
    if not root.is_dir() or not case_id: return None
    exact=root/case_id
    if exact.is_dir(): return exact
    key=_case_key(case_id)
    try:
        for child in root.iterdir():
            if child.is_dir() and _case_key(child.name)==key:
                return child
    except Exception: pass
    return None


def candidate_patient_roots(cfg):
    roots=[]
    def add(x):
        if not x: return
        try: q=Path(str(x)).expanduser().resolve()
        except Exception: return
        if q not in roots: roots.append(q)
    add(cfg.get("patient_root"))
    # Known project layouts: template project and desktop raw-data root.
    try:
        t=Path(str(cfg.get("template") or "")).expanduser().resolve().parent
        add(t/"AllSubjects1"); add(t/"AllSubjects")
    except Exception: pass
    try:
        o=Path(str(cfg.get("output_root") or "")).expanduser().resolve().parent
        add(o/"AllSubjects1"); add(o/"AllSubjects")
    except Exception: pass
    add(Path.home() / "Desktop" / "AllSubjects1")
    add(Path.home() / "Desktop" / "AllSubjects")
    return roots


def locate_case_source(cfg, case_id):
    for root in candidate_patient_roots(cfg):
        hit=_match_case_dir(root,case_id)
        if hit is not None: return root,hit
    root=Path(str(cfg.get("patient_root") or "")).expanduser()
    return root, root/case_id if case_id else root/"__none__"


def _match_output_case_dir(root: Path, case_id: str):
    return _match_case_dir(root,case_id)


def locate_existing_case_output(cfg, case_id):
    """Find historical CAT12 output using exact or normalized case-folder matching."""
    for root in candidate_output_roots(cfg):
        case_dir=_match_output_case_dir(root,case_id)
        if case_dir is None: continue
        nifti=case_dir/"_dcm2niix_out"
        if has_reusable_cat(nifti, root, case_dir.name) or has_reusable_cat(nifti, root, case_id):
            return root, nifti
    root=output_root_for(cfg)
    case_dir=_match_output_case_dir(root,case_id)
    if case_dir is not None: return root,case_dir/"_dcm2niix_out"
    return root, root/case_id/"_dcm2niix_out"


def _directory_access_probe(path: Path, sample_limit=3):
    """Explicitly test whether this process can enumerate a directory.

    macOS Desktop/Documents privacy can allow Path.is_dir() while denying directory
    enumeration to an app process. pathlib.glob/os.walk may then look like an empty
    folder, which older NMA versions incorrectly classified as "未录入".
    """
    p=Path(path).expanduser()
    out={"exists":False,"is_dir":False,"readable":False,"error":"","samples":[]}
    try:
        out["exists"]=p.exists(); out["is_dir"]=p.is_dir()
    except Exception as e:
        out["error"]=f"{type(e).__name__}: {e}"; return out
    if not out["is_dir"]: return out
    try:
        with os.scandir(p) as it:
            for i,ent in enumerate(it):
                if i < sample_limit: out["samples"].append(ent.name)
                if i >= sample_limit: break
        out["readable"]=True
    except Exception as e:
        out["error"]=f"{type(e).__name__}: {e}"
    return out


def _dicom_hint(source: Path, max_depth=8, max_header_checks=2000):
    """Robust DICOM presence test that never hides macOS permission failures."""
    probe=_directory_access_probe(source)
    if not probe.get("is_dir"):
        return {"kind":"none","files":0,"matched":"","access":probe}
    if not probe.get("readable"):
        return {"kind":"denied","files":0,"matched":"","error":probe.get("error","") or "目录不可读取","access":probe}
    files_seen=0; header_checks=0
    base=source.expanduser()
    def walk_dir(cur: Path, depth: int):
        nonlocal files_seen, header_checks
        try:
            entries=list(os.scandir(cur))
        except PermissionError as e:
            raise PermissionError(f"无法读取目录 {cur}: {e}") from e
        except OSError as e:
            raise OSError(f"无法扫描目录 {cur}: {e}") from e
        for ent in entries:
            try:
                if ent.is_dir(follow_symlinks=False):
                    if depth < max_depth: yield from walk_dir(Path(ent.path), depth+1)
                    continue
                if not ent.is_file(follow_symlinks=False): continue
            except OSError:
                continue
            files_seen+=1
            p=Path(ent.path)
            ext=p.suffix.lower()
            if ext in (".dcm",".ima",".dicom"):
                yield p; return
            if header_checks < max_header_checks:
                header_checks+=1
                try:
                    with p.open("rb") as f: head=f.read(132)
                    if len(head)>=132 and head[128:132]==b"DICM":
                        yield p; return
                except PermissionError as e:
                    raise PermissionError(f"无法读取文件 {p}: {e}") from e
                except Exception:
                    pass
    try:
        for hit in walk_dir(base,0):
            return {"kind":"yes","files":files_seen,"matched":str(hit),"access":probe}
    except PermissionError as e:
        return {"kind":"denied","files":files_seen,"matched":"","error":str(e),"access":probe}
    except Exception as e:
        return {"kind":"unknown","files":files_seen,"error":f"{type(e).__name__}: {e}","matched":"","access":probe}
    return {"kind":"none" if files_seen==0 else "unknown","files":files_seen,"matched":"","access":probe}


def imaging_info(cfg, case_id):
    source_root,source=locate_case_source(cfg,case_id)
    out_root,nifti=locate_existing_case_output(cfg,case_id)
    source_probe=_directory_access_probe(source)
    output_case=nifti.parent if nifti.name=="_dcm2niix_out" else nifti
    output_probe=_directory_access_probe(output_case) if output_case.exists() else {"exists":False,"is_dir":False,"readable":False,"error":"","samples":[]}

    # Only attempt CAT12 content discovery if the case output can be enumerated.
    reusable=False; reuse_evidence=[]
    cat_error=""
    if output_case.is_dir() and not output_probe.get("readable"):
        cat_error=output_probe.get("error","") or "CAT12 病例目录不可读取"
    else:
        try:
            reuse_evidence=cat_reuse_evidence(nifti,out_root,case_id)
            reusable=any(not x.endswith(" [report]") for x in reuse_evidence)
        except PermissionError as e: cat_error=f"PermissionError: {e}"
        except OSError as e: cat_error=f"{type(e).__name__}: {e}"

    niis=[]; pngs=[]
    if nifti.is_dir() and output_probe.get("readable",True):
        try:
            niis=sorted(nifti.glob("*.nii*")); pngs=sorted(nifti.glob("*_3views_nice.png"))
        except Exception as e:
            if not cat_error: cat_error=f"{type(e).__name__}: {e}"
    hint=_dicom_hint(source)
    permission_denied=(hint.get("kind")=="denied") or bool(cat_error and output_case.is_dir())
    if reusable: status="已有CAT12结果"
    elif hint.get("kind")=="yes": status="影像已录入"
    elif niis: status="影像已录入"
    elif permission_denied: status="需要授权"
    elif not source.is_dir(): status="未录入"
    elif hint.get("kind")=="none": status="未录入"
    else: status="待检测"
    permission_msg=""
    if hint.get("kind")=="denied": permission_msg=hint.get("error","") or "患者病例目录没有读取权限"
    elif cat_error: permission_msg=cat_error
    return {"source_exists":source.is_dir(),"source":str(source),"source_root":str(source_root),
            "output_root":str(out_root),"nifti_dir":str(nifti),"cat_exists":reusable,
            "nii_exists":bool(niis),"png_exists":bool(pngs),"status":status,
            "dicom_hint":hint,"source_access":source_probe,"output_access":output_probe,
            "permission_denied":permission_denied,"permission_message":permission_msg,
            "cat_evidence":reuse_evidence,
            "brain_png":str(pngs[0]) if pngs else ""}


def require_components(status, names):
    bad=[n for n in names if not status.get("items",{}).get(n,False)]
    if bad: raise RuntimeError("当前病例需要以下环境，但尚未就绪："+"、".join(bad))


def _finish_no_imaging(job, patient, interp, reports, pdf, cfg, reason="核磁影像未录入"):
    set_step(job,"imaging","skip",reason); set_step(job,"cat12","skip","无影像，跳过")
    set_step(job,"volume","skip","无影像，跳过"); set_step(job,"brain","skip","无影像，跳过")
    set_stage(job,"未录入核磁影像：生成基本信息与量表报告",86)
    set_step(job,"report","active")
    background=resolve_template_background(cfg,job)
    set_stage(job,"生成 PDF 报告",94)
    pdf,preview=compose_report(patient,interp,None,None,pdf,background,reason)
    manifest=pdf.with_suffix(".json")
    try: manifest.write_text(json.dumps({"app_version":APP_VERSION,"patient":patient,"interp":interp,"imaging_status":"未录入","generated_at":datetime.now().isoformat()},ensure_ascii=False,indent=2),encoding="utf-8")
    except Exception: pass
    set_step(job,"report","done")
    with STATE_LOCK:
        job.update({"status":"done","stage":"报告生成完成（影像未录入）","progress":100,"report":str(pdf),"preview":str(preview),
                    "output_dir":str(reports),"patient":patient,"interp":interp,"volumes":None,"imaging_status":"未录入","brain_png":""})
    job_log(job,"完成："+str(pdf))


def _looks_like_no_dicom_error(exc):
    t=str(exc).lower()
    return ("unable to find any dicom images" in t) or ("no dicom" in t and "image" in t)


def run_job(job_id, patient):
    with STATE_LOCK: job=STATE["jobs"][job_id]
    try:
        cfg=load_config(); patient=normalize_patient_data(patient); case_id=patient["case_id"]
        if not case_id: raise RuntimeError("病例编号不能为空；它用于匹配患者影像目录。")
        job["case_id"]=case_id
        set_step(job,"env","active"); set_stage(job,"检查系统环境",3)
        status=dependency_status(cfg)
        require_components(status,["Python解释器","报告绘图组件","报告模板","患者数据目录"])
        try:
            configured_py=Path(str(cfg.get("python") or "")).expanduser().resolve(); running_py=Path(sys.executable).resolve()
            if configured_py != running_py: raise RuntimeError(f"Python 解释器已修改为 {configured_py}。请退出并重新打开 NeuroMorph Assessment 后再运行。")
        except RuntimeError: raise
        except Exception: pass
        set_step(job,"env","done")

        info=imaging_info(cfg,case_id); output_root=Path(info["output_root"]); nifti_dir=Path(info["nifti_dir"])
        source=Path(info["source"]); reports=output_root/"NMA_reports"; pdf=reports/f"{case_id}.pdf"
        set_step(job,"patient","active"); set_stage(job,"读取患者信息与量表",7)
        interp=interpret_scales(patient)
        for k in ["MCCA","MMSE","HAMA","HAMD"]: job_log(job,f"{k}: {fmt_score(patient.get(k))} → {interp[k]}")
        set_step(job,"patient","done")
        job_log(job,f"影像状态：{info['status']}")
        job_log(job,f"患者影像目录：{source}")
        job_log(job,f"CAT12结果目录：{nifti_dir}")
        if info.get("permission_denied"):
            job_log(job,"目录访问权限不足："+(info.get("permission_message") or "macOS 未允许 NMA 读取该目录"))
            raise RuntimeError("NMA 没有读取病例目录/CAT12结果目录的权限。请打开‘高级设置’，使用目录右侧的‘选择/授权’按钮分别重新选择患者数据总目录和 CAT12 结果总目录，然后保存设置。")
        job_log(job,f"CAT12结果总目录（高级设置）：{output_root}")
        if info.get("cat_evidence"):
            job_log(job,"已有 CAT12 证据："+" | ".join(info.get("cat_evidence",[])[:6]))

        # Reusable CAT12 output is authoritative and never needs raw DICOM again.
        if info["cat_exists"] and bool(cfg.get("reuse_existing",True)):
            set_step(job,"imaging","done","复用既有转换结果")
            set_step(job,"cat12","done","复用已有 CAT12")
            set_stage(job,"复用已有 CAT12 结果",20)
            job_log(job,"检测到可提取体积的完整 CAT12 结果，跳过 DICOM 转换和 CAT12 重算。")
            t1=None
            try:
                if info["nii_exists"]: t1=pick_t1(nifti_dir)
            except Exception as e:
                job_log(job,"已有 CAT12 结果，但未找到可用于三视图的 T1："+str(e))
            set_step(job,"volume","active"); set_stage(job,"提取 TIV / GM / WM",72)
            volumes=get_volumes(nifti_dir,output_root,case_id)
            set_step(job,"volume","done")
            job_log(job,"体积来源："+str(volumes.get("source","")))
            job_log(job,f"TIV={volumes['TIV']:.2f}, GM={volumes['GM']:.2f}, WM={volumes['WM']:.2f} cm³")
            set_step(job,"brain","active"); set_stage(job,"生成/复用核磁三视图",82)
            pngs=sorted(nifti_dir.glob("*_3views_nice.png"))
            brain_png=pngs[0] if pngs else (build_3view_figure_nice(t1) if t1 else None)
            if brain_png: set_step(job,"brain","done")
            else: set_step(job,"brain","skip","未找到 T1，报告保留体积结果")
            set_step(job,"report","active"); set_stage(job,"生成 PDF 报告",94)
            background=resolve_template_background(cfg,job)
            pdf,preview=compose_report(patient,interp,volumes,brain_png,pdf,background,"核磁三视图未生成")
            set_step(job,"report","done")
            with STATE_LOCK:
                job.update({"status":"done","stage":"报告生成完成","progress":100,"report":str(pdf),"preview":str(preview),"output_dir":str(reports),
                            "patient":patient,"interp":interp,"volumes":volumes,"imaging_status":"已完成","brain_png":str(brain_png or "")})
            job_log(job,"完成："+str(pdf)); return

        # Defensive re-check immediately before any DICOM conversion. Existing CAT12 must never be overwritten/recomputed.
        try:
            fresh_root,fresh_nifti=locate_existing_case_output(cfg,case_id)
            fresh_ev=cat_reuse_evidence(fresh_nifti,fresh_root,case_id)
            fresh_reusable=any(not x.endswith(" [report]") for x in fresh_ev)
        except Exception as e:
            fresh_reusable=False; fresh_ev=[]
            job_log(job,"CAT12 复用二次检查："+str(e))
        if fresh_reusable:
            job_log(job,"二次检查发现已有 CAT12 可提取结果，停止任何 DICOM/CAT12 重算。")
            info["cat_exists"]=True; info["cat_evidence"]=fresh_ev; info["nifti_dir"]=str(fresh_nifti); info["output_root"]=str(fresh_root)
            output_root=Path(fresh_root); nifti_dir=Path(fresh_nifti)
            set_step(job,"imaging","done","复用既有转换结果"); set_step(job,"cat12","done","复用已有 CAT12")
            set_stage(job,"复用已有 CAT12 结果",20)
            t1=None
            try: t1=pick_t1(nifti_dir)
            except Exception as e: job_log(job,"已有 CAT12 结果，但未找到可用于三视图的 T1："+str(e))
            set_step(job,"volume","active"); set_stage(job,"提取 TIV / GM / WM",72)
            volumes=get_volumes(nifti_dir,output_root,case_id); set_step(job,"volume","done")
            job_log(job,"体积来源："+str(volumes.get("source","")))
            job_log(job,f"TIV={volumes['TIV']:.2f}, GM={volumes['GM']:.2f}, WM={volumes['WM']:.2f} cm³")
            set_step(job,"brain","active"); set_stage(job,"生成/复用核磁三视图",82)
            pngs=sorted(nifti_dir.glob("*_3views_nice.png")); brain_png=pngs[0] if pngs else (build_3view_figure_nice(t1) if t1 else None)
            set_step(job,"brain","done" if brain_png else "skip","" if brain_png else "未找到 T1，报告保留体积结果")
            set_step(job,"report","active"); set_stage(job,"生成 PDF 报告",94)
            background=resolve_template_background(cfg,job); pdf,preview=compose_report(patient,interp,volumes,brain_png,pdf,background,"核磁三视图未生成")
            set_step(job,"report","done")
            with STATE_LOCK:
                job.update({"status":"done","stage":"报告生成完成","progress":100,"report":str(pdf),"preview":str(preview),"output_dir":str(reports),"patient":patient,"interp":interp,"volumes":volumes,"imaging_status":"已完成","brain_png":str(brain_png or "")})
            job_log(job,"完成："+str(pdf)); return

        # No reusable CAT12 output. If the patient folder is absent/empty, this is a normal unrecorded case.
        if info["status"]=="未录入" and not info["nii_exists"]:
            _finish_no_imaging(job,patient,interp,reports,pdf,cfg,"核磁影像未录入"); return

        nifti_dir.mkdir(parents=True,exist_ok=True)
        # Reuse already converted NIfTI if a valid T1 is there.
        t1=None
        if info["nii_exists"]:
            try:
                t1=pick_t1(nifti_dir)
                set_step(job,"imaging","done","复用已有 NIfTI")
                set_stage(job,"复用已转换 T1 NIfTI",18)
                job_log(job,"已存在合格 T1 NIfTI，跳过 dcm2niix。")
            except Exception as e:
                job_log(job,"已有 NIfTI 但未找到合格 T1，将尝试重新转换 DICOM："+str(e))

        if t1 is None:
            if not source.is_dir():
                _finish_no_imaging(job,patient,interp,reports,pdf,cfg,"核磁影像未录入"); return
            require_components(status,["dcm2niix","Python影像依赖"])
            set_step(job,"imaging","active"); set_stage(job,"匹配/转换 DICOM 影像",12)
            dcm=cfg.get("dcm2niix") or shutil.which("dcm2niix")
            try:
                run_cmd([dcm,"-ba","y","-z","y","-b","y","-f","%p_s%s_e%e_run%r_%t","-o",str(nifti_dir),str(source)],job)
            except RuntimeError as e:
                if _looks_like_no_dicom_error(e):
                    job_log(job,"患者文件夹存在，但 dcm2niix 未检测到任何 DICOM；按“未录入”继续生成报告。")
                    _finish_no_imaging(job,patient,interp,reports,pdf,cfg,"核磁影像未录入"); return
                raise
            set_stage(job,"自动识别 T1 MRI",20)
            try: t1=pick_t1(nifti_dir)
            except Exception as e:
                # Conversion ran, but no valid T1: this is a genuine imaging-data error, not 'unrecorded'.
                raise RuntimeError("检测到影像数据，但未找到满足既有规则的 T1 MRI："+str(e))
            set_step(job,"imaging","done")

        require_components(status,["MATLAB","SPM12","CAT12","Python影像依赖"])
        t1_for_cat=t1
        if t1.suffix==".gz":
            unz=t1.with_suffix("")
            if not unz.exists():
                with gzip.open(t1,"rb") as src, open(unz,"wb") as dst: shutil.copyfileobj(src,dst)
            t1_for_cat=unz
        set_step(job,"cat12","active"); set_stage(job,"CAT12 脑结构分析（此步骤耗时较长）",30)
        run_cat12(nifti_dir,t1_for_cat,cfg,job)
        set_step(job,"cat12","done")

        set_step(job,"volume","active"); set_stage(job,"提取 TIV / GM / WM",72)
        volumes=get_volumes(nifti_dir,output_root,case_id)
        set_step(job,"volume","done")
        job_log(job,"体积来源："+str(volumes.get("source","")))
        job_log(job,f"TIV={volumes['TIV']:.2f}, GM={volumes['GM']:.2f}, WM={volumes['WM']:.2f} cm³")
        set_step(job,"brain","active"); set_stage(job,"生成核磁三视图",82)
        pngs=sorted(nifti_dir.glob("*_3views_nice.png")); brain_png=pngs[0] if pngs else build_3view_figure_nice(t1)
        set_step(job,"brain","done")
        set_step(job,"report","active"); set_stage(job,"生成最终 PDF 报告",94)
        background=resolve_template_background(cfg,job)
        pdf,preview=compose_report(patient,interp,volumes,brain_png,pdf,background)
        set_step(job,"report","done")
        with STATE_LOCK:
            job.update({"status":"done","stage":"报告生成完成","progress":100,"report":str(pdf),"preview":str(preview),"output_dir":str(reports),
                        "patient":patient,"interp":interp,"volumes":volumes,"imaging_status":"已完成","brain_png":str(brain_png)})
        job_log(job,"完成："+str(pdf))
    except Exception as e:
        with STATE_LOCK:
            job["status"]="error"; job["stage"]="生成失败"; job["error"]=str(e)
        job_log(job,"ERROR: "+str(e)); job_log(job,traceback.format_exc())


# ------------------------------ Web UI ------------------------------
HTML = (RES_DIR / "ui.html").read_text(encoding="utf-8")


def json_response(handler, obj, status=200):
    b=json.dumps(obj,ensure_ascii=False).encode("utf-8")
    handler.send_response(status); handler.send_header("Content-Type","application/json; charset=utf-8"); handler.send_header("Content-Length",str(len(b))); handler.end_headers(); handler.wfile.write(b)


def read_json(handler):
    n=int(handler.headers.get("Content-Length","0") or 0); raw=handler.rfile.read(n) if n else b"{}"; return json.loads(raw.decode("utf-8") or "{}")


def parse_multipart_file(handler):
    ctype=handler.headers.get("Content-Type","")
    m=re.search(r"boundary=(.+)",ctype)
    if not m: raise RuntimeError("上传格式错误")
    boundary=m.group(1).strip().strip('"').encode()
    n=int(handler.headers.get("Content-Length","0")); data=handler.rfile.read(n)
    for part in data.split(b"--"+boundary):
        if b"Content-Disposition" not in part: continue
        head,sep,body=part.partition(b"\r\n\r\n")
        if not sep: continue
        mm=re.search(br'filename="([^"]+)"',head)
        if not mm: continue
        name=mm.group(1).decode("utf-8","replace"); body=body.rstrip(b"\r\n-")
        return name,body
    raise RuntimeError("未读取到上传文件")


class Handler(BaseHTTPRequestHandler):
    server_version="NMA/0.10"
    def log_message(self,*args): pass
    def _send_file(self,p,ctype):
        p=Path(p)
        if not p.is_file(): return json_response(self,{"error":"文件不存在"},404)
        b=p.read_bytes(); self.send_response(200); self.send_header("Content-Type",ctype); self.send_header("Cache-Control","no-store"); self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        u=urllib.parse.urlparse(self.path)
        try:
            if u.path=="/":
                b=HTML.encode("utf-8"); self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8"); self.send_header("Cache-Control","no-store"); self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b); return
            if u.path=="/api/version": return json_response(self,{"version":APP_VERSION,"pid":os.getpid()})
            if u.path=="/api/config":
                cfg=load_config(); return json_response(self,{"config":cfg,"status":dependency_status(cfg)})
            if u.path=="/api/job":
                q=urllib.parse.parse_qs(u.query); jid=(q.get("id") or [""])[0]
                with STATE_LOCK: j=STATE["jobs"].get(jid)
                if not j: return json_response(self,{"error":"任务不存在"},404)
                safe={k:v for k,v in j.items() if k not in ("thread",)}; return json_response(self,safe)
            if u.path=="/api/job-brain":
                q=urllib.parse.parse_qs(u.query); jid=(q.get("id") or [""])[0]
                with STATE_LOCK: j=STATE["jobs"].get(jid)
                if not j or not j.get("brain_png"): return json_response(self,{"error":"暂无脑影像预览"},404)
                return self._send_file(j["brain_png"],"image/png")
            if u.path=="/api/patient-brain":
                q=urllib.parse.parse_qs(u.query); cid=(q.get("case_id") or [""])[0]; cfg=load_config(); info=imaging_info(cfg,cid)
                if not info.get("brain_png"): return json_response(self,{"error":"暂无脑影像预览"},404)
                return self._send_file(info["brain_png"],"image/png")
            return json_response(self,{"error":"Not found"},404)
        except Exception as e: return json_response(self,{"error":str(e)},500)
    def do_POST(self):
        u=urllib.parse.urlparse(self.path)
        try:
            if u.path=="/api/config":
                incoming=read_json(self); cfg=load_config()
                for k in DEFAULT_CONFIG:
                    if k in incoming: cfg[k]=incoming[k]
                save_config(cfg); return json_response(self,{"ok":True,"config":cfg,"status":dependency_status(cfg)})
            if u.path=="/api/upload-excel":
                name,body=parse_multipart_file(self)
                if not name.lower().endswith(".xlsx"): raise RuntimeError("目前仅支持 .xlsx")
                p=UPLOAD_DIR/(datetime.now().strftime("%Y%m%d_%H%M%S_")+Path(name).name); p.write_bytes(body)
                recs=read_xlsx_first_sheet(p); rows=[normalize_patient_row(x) for x in recs]; rows=[x for x in rows if x["case_id"]]
                cfg=load_config()
                for row in rows:
                    info=imaging_info(cfg,row["case_id"]); row["imaging_status"]=info["status"]; row["imaging_exists"]=info["source_exists"] or info["cat_exists"]
                with STATE_LOCK: STATE["excel_rows"]=rows; STATE["excel_path"]=str(p)
                return json_response(self,{"rows":rows,"count":len(rows)})
            if u.path=="/api/refresh-patients":
                cfg=load_config()
                with STATE_LOCK:
                    rows=[dict(x) for x in STATE.get("excel_rows",[])]
                for row in rows:
                    info=imaging_info(cfg,row.get("case_id","")); row["imaging_status"]=info["status"]; row["imaging_exists"]=info["source_exists"] or info["cat_exists"]
                with STATE_LOCK: STATE["excel_rows"]=rows
                return json_response(self,{"rows":rows,"count":len(rows)})
            if u.path=="/api/interpret":
                p=normalize_patient_data(read_json(self)); cfg=load_config(); info=imaging_info(cfg,p["case_id"])
                return json_response(self,{"interp":interpret_scales(p),"match":{"exists":info["source_exists"] or info["cat_exists"],"path":info["source"],"status":info["status"],"cat_exists":info["cat_exists"],"brain_png":bool(info["brain_png"]),"output_root":info["output_root"],"nifti_dir":info["nifti_dir"],"dicom_hint":info.get("dicom_hint",{}),"source_access":info.get("source_access",{}),"output_access":info.get("output_access",{}),"permission_denied":info.get("permission_denied",False),"permission_message":info.get("permission_message",""),"cat_evidence":info.get("cat_evidence",[])}})
            if u.path=="/api/generate":
                p=normalize_patient_data(read_json(self)); jid=uuid.uuid4().hex[:12]
                job={"id":jid,"status":"running","stage":"任务已创建","progress":0,"logs":[],"created":datetime.now().isoformat(),"case_id":p.get("case_id","")}
                _init_steps(job)
                with STATE_LOCK: STATE["jobs"][jid]=job
                t=threading.Thread(target=run_job,args=(jid,p),daemon=True); job["thread"]=t; t.start(); return json_response(self,{"job_id":jid})
            if u.path=="/api/open":
                x=read_json(self); jid=x.get("job_id"); typ=x.get("type")
                with STATE_LOCK: j=STATE["jobs"].get(jid)
                if not j: raise RuntimeError("任务不存在")
                target=j.get("report") if typ=="report" else j.get("output_dir")
                if not target: raise RuntimeError("目标尚未生成")
                subprocess.Popen(["/usr/bin/open",target]); return json_response(self,{"ok":True})
            return json_response(self,{"error":"Not found"},404)
        except Exception as e: return json_response(self,{"error":str(e)},500)


def find_port(start=8765):
    for p in range(start,start+30):
        s=socket.socket();
        try: s.bind(("127.0.0.1",p)); s.close(); return p
        except OSError: s.close()
    return 0


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--port",type=int,default=0)
    ap.add_argument("--no-browser",action="store_true")
    ap.add_argument("--server-file",default="")
    ap.add_argument("--token",default="")
    args=ap.parse_args()
    port=args.port or find_port()
    if not port: raise RuntimeError("无法找到可用本地端口")
    server=ThreadingHTTPServer(("127.0.0.1",port),Handler)
    url=f"http://127.0.0.1:{port}/"
    server_file=Path(args.server_file).expanduser() if args.server_file else (SUPPORT_DIR/f"server_{os.getpid()}.json")
    server_file.parent.mkdir(parents=True,exist_ok=True)
    payload={"url":url,"pid":os.getpid(),"version":APP_VERSION,"token":args.token,"started_at":datetime.now().isoformat()}
    server_file.write_text(json.dumps(payload,ensure_ascii=False),encoding="utf-8")
    print(f"NeuroMorph Assessment {APP_VERSION} -> {url}",flush=True)
    if not args.no_browser: threading.Timer(.7,lambda: webbrowser.open(url)).start()
    def stop(*_): threading.Thread(target=server.shutdown,daemon=True).start()
    def cleanup_serverfile():
        try:
            if server_file.is_file():
                j=json.loads(server_file.read_text(encoding="utf-8"))
                if int(j.get("pid",-1))==os.getpid(): server_file.unlink(missing_ok=True)
        except Exception: pass
    signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
    try: server.serve_forever(poll_interval=.5)
    finally: cleanup_serverfile()


if __name__=="__main__": main()
