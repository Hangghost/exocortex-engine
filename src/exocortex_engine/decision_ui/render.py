"""Deterministic decision-page renderer.

純函數：吃 items + action vocabulary，吐一份**零外部依賴的單檔 HTML** 字串。
不寫檔、不連網、不碰 git——寫檔是 caller 的責任。

零外部依賴是硬約束而非偏好：產物必須能被 scp / AirDrop / email 搬到任何一台
機器上用瀏覽器直接開（`file://`、無網路、無 server）。任何 CDN、外部字型或
fetch 都會讓它在離線或他機失效，而「在 A 機判斷、B 機執行」正是本模式存在的
理由。

Determinism：renderer 內**不得讀時鐘**（否則同輸入不同輸出）。`generated_on`
由 caller 傳入、只用來導出 localStorage key；決策檔的 `generated_at` 由頁面在
export 當下以 JS 蓋章，不影響 render 出來的 bytes。

Item 顯示契約（除 id 外皆選填）：

    {<id_key>: str,        # 唯一鍵，進 decisions 的 id
     "label":  str,        # 標題列
     "sublabel": str,      # 次要資訊列（meta）
     "body":   str,        # 主要內容區塊（保留換行）
     "url":    str,        # 有值時 label 變成連結
     "scope":  dict,       # 原樣帶進 decisions 的 scope
     <suggested_key>: str} # AI/heuristic 建議的 action

Vocabulary 元素可以是字串，或 `{"action", "label", "requires_note", "hint"}`。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

__all__ = ["render_decision_page"]


def _norm_vocab(vocabulary: Sequence[Any]) -> list[dict]:
    out: list[dict] = []
    for v in vocabulary:
        if isinstance(v, str):
            out.append({"action": v, "label": v, "requires_note": False, "hint": ""})
        elif isinstance(v, Mapping):
            action = str(v["action"])
            out.append({
                "action": action,
                "label": str(v.get("label") or action),
                "requires_note": bool(v.get("requires_note", False)),
                "hint": str(v.get("hint") or ""),
            })
        else:
            raise TypeError(f"vocabulary 元素必須是 str 或 mapping，實得 {type(v).__name__}")
    if not out:
        raise ValueError("vocabulary 不可為空")
    return out


def _norm_filters(filters: Sequence[Any]) -> list[dict]:
    out: list[dict] = []
    for f in filters:
        if isinstance(f, str):
            out.append({"key": f, "label": f, "sort": False})
        elif isinstance(f, Mapping):
            key = str(f["key"])
            out.append({
                "key": key,
                "label": str(f.get("label") or key),
                "sort": bool(f.get("sort", False)),
            })
        else:
            raise TypeError(f"filters 元素必須是 str 或 mapping，實得 {type(f).__name__}")
    return out


def _json_for_script(value: Any) -> str:
    """JSON 字面量，安全內嵌進 <script>。

    `sort_keys` 保證 determinism；`</script>` 注入靠把 `<` 轉成 `\\u003c` 擋掉
    （合法的 JSON 字串跳脫，parse 回來一模一樣）。
    """
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"))
            .replace("<", "\\u003c"))


_CSS = """
:root{--bg:#fafaf8;--card:#fff;--fg:#1a1a1a;--mut:#6b6b6b;--line:#e4e2dc;
--acc:#3b6ea5;--ok:#2f7d4f;--warn:#b3592c}
@media(prefers-color-scheme:dark){:root{--bg:#16181d;--card:#1f2229;--fg:#e8e6e1;
--mut:#9a988f;--line:#33363e;--acc:#7aa7d4;--ok:#69b789;--warn:#d98d63}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.55 -apple-system,"PingFang TC",sans-serif}
.wrap{max-width:1000px;margin:0 auto;padding:24px 16px 120px}
h1{font-size:20px;margin:0 0 4px}
.sub{color:var(--mut);font-size:13px;margin-bottom:16px}
.bar{position:sticky;top:0;background:var(--bg);padding:10px 0;
border-bottom:1px solid var(--line);z-index:5;display:flex;gap:8px;
flex-wrap:wrap;align-items:center}
.bar input[type=search]{flex:1;min-width:160px;padding:6px 10px;
border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg)}
.bar select,.bar button{padding:6px 10px;border:1px solid var(--line);
border-radius:8px;background:var(--card);color:var(--fg);cursor:pointer;font-size:13px}
.stats{font-size:13px;color:var(--mut);margin:10px 0}
.item{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:12px 14px;margin:10px 0}
.item.decided{border-left:3px solid var(--ok)}
.t{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}
.t a{color:var(--acc);text-decoration:none;font-weight:600}
.t a:hover{text-decoration:underline}
.t .plain{font-weight:600}
.badge{font-size:11px;padding:1px 7px;border-radius:99px;
border:1px solid var(--line);color:var(--mut)}
.meta{color:var(--mut);font-size:12.5px;margin:2px 0}
.body{margin:6px 0;white-space:pre-wrap;font-size:14px}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.chip{font-size:12.5px;padding:3px 10px;border-radius:99px;
border:1px solid var(--line);background:transparent;color:var(--fg);cursor:pointer}
.chip.sel{background:var(--acc);border-color:var(--acc);color:#fff}
.chip.sug:not(.sel){border-style:dashed;border-color:var(--acc);color:var(--acc)}
.note{margin-top:6px;width:100%;padding:6px 10px;border:1px solid var(--warn);
border-radius:8px;background:var(--bg);color:var(--fg)}
.foot{position:fixed;bottom:0;left:0;right:0;background:var(--card);
border-top:1px solid var(--line);padding:10px 16px;display:flex;gap:10px;
align-items:center;justify-content:space-between;flex-wrap:wrap}
.primary{background:var(--acc);color:#fff;border:0;padding:8px 18px;
border-radius:8px;cursor:pointer}
"""

_JS = r"""
var $=function(id){return document.getElementById(id)};
var sel=JSON.parse(localStorage.getItem(KEY)||"{}");
var NOTE=(function(){var m={};VOCAB.forEach(function(v){m[v.action]=v.requires_note});return m})();
function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]})}
function visible(){
  var q=($("q").value||"").toLowerCase();
  var st=$("st").value, out=[];
  for(var i=0;i<DATA.length;i++){
    var it=DATA[i], s=sel[it.__id]||{}, decided=!!s.action;
    if(st==="undecided"&&decided)continue;
    if(st==="decided"&&!decided)continue;
    var skip=false;
    for(var f=0;f<FILTERS.length;f++){
      var fv=$("f_"+FILTERS[f].key).value;
      if(fv&&String(it[FILTERS[f].key]||"")!==fv){skip=true;break}
    }
    if(skip)continue;
    if(q){
      var hay=(it.label+" "+it.sublabel+" "+it.body+" "+it.__id).toLowerCase();
      if(hay.indexOf(q)<0)continue;
    }
    out.push(i);
  }
  var sk=$("so")?$("so").value:"";
  if(sk)out.sort(function(a,b){return (Number(DATA[b][sk])||0)-(Number(DATA[a][sk])||0)});
  return out;
}
function render(){
  var idx=visible(), html="";
  for(var n=0;n<idx.length;n++){
    var i=idx[n], it=DATA[i], s=sel[it.__id]||{}, decided=!!s.action;
    var chips="";
    for(var v=0;v<VOCAB.length;v++){
      var a=VOCAB[v];
      chips+='<button class="chip'+(s.action===a.action?" sel":"")+
        (it.__suggested===a.action?" sug":"")+'" title="'+esc(a.hint)+
        '" onclick="pick('+i+',\''+a.action+'\')">'+esc(a.label)+"</button>";
    }
    var head=it.url?'<a href="'+esc(it.url)+'" target="_blank" rel="noreferrer">'+
      esc(it.label)+"</a>":'<span class="plain">'+esc(it.label)+"</span>";
    html+='<div class="item'+(decided?" decided":"")+'"><div class="t">'+head+
      '<span class="badge">'+esc(it.__id)+"</span></div>"+
      (it.sublabel?'<div class="meta">'+esc(it.sublabel)+"</div>":"")+
      (it.body?'<div class="body">'+esc(it.body)+"</div>":"")+
      '<div class="chips">'+chips+"</div>"+
      '<input class="note" id="n'+i+'" placeholder="note（此 action 必填）" '+
      'style="display:'+(NOTE[s.action]?"block":"none")+'" value="'+
      esc(s.note||"")+'" onchange="setNote('+i+',this.value)">'+"</div>";
  }
  $("list").innerHTML=html;
  var done=0;
  for(var k=0;k<DATA.length;k++)if(sel[DATA[k].__id]&&sel[DATA[k].__id].action)done++;
  $("progress").textContent="已決定 "+done+" / "+DATA.length;
  $("stats").textContent="顯示 "+idx.length+" 條（共 "+DATA.length+" 條）";
}
function save(){localStorage.setItem(KEY,JSON.stringify(sel))}
window.pick=function(i,a){
  var id=DATA[i].__id, cur=sel[id]&&sel[id].action;
  sel[id]=Object.assign({},sel[id],{action:cur===a?null:a});
  save();render();
};
window.setNote=function(i,v){
  var id=DATA[i].__id; sel[id]=Object.assign({},sel[id],{note:v}); save();
};
function doExport(){
  var dec=[], missing=0;
  for(var i=0;i<DATA.length;i++){
    var it=DATA[i], s=sel[it.__id];
    if(!s||!s.action)continue;
    var note=(s.note||"").trim();
    if(NOTE[s.action]&&!note){missing++;continue}
    dec.push({id:it.__id,scope:it.__scope||{},action:s.action,note:note||null});
  }
  if(missing)alert(missing+" 條的 action 需要 note 但未填，已略過——補完後重新 export");
  var doc={schema_version:1,kind:KIND,generated_at:new Date().toISOString(),decisions:dec};
  var blob=new Blob([JSON.stringify(doc,null,1)],{type:"application/json"});
  var a=document.createElement("a");
  a.href=URL.createObjectURL(blob);a.download=KIND+"_decisions_"+GENERATED_ON+".json";a.click();
}
"""

_BULK_JS = r"""
function bulkAdopt(){
  var idx=visible(), n=0;
  for(var k=0;k<idx.length;k++){
    var it=DATA[idx[k]];
    if(it.__suggested&&!(sel[it.__id]&&sel[it.__id].action)){
      sel[it.__id]=Object.assign({},sel[it.__id],{action:it.__suggested});n++;
    }
  }
  save();render();
}
"""


def render_decision_page(
    items: Sequence[Mapping[str, Any]],
    vocabulary: Sequence[Any],
    *,
    kind: str,
    title: str,
    id_key: str,
    generated_on: date | str,
    suggested_key: str | None = None,
    filters: Sequence[Any] = (),
    allow_bulk_adopt: bool = True,
) -> str:
    """Render a self-contained decision page. Pure — no I/O, no clock reads."""
    if not kind:
        raise ValueError("kind 不可為空（decisions 檔靠它擋餵錯的 applier）")
    vocab = _norm_vocab(vocabulary)
    filt = _norm_filters(filters)
    gen_on = generated_on.isoformat() if isinstance(generated_on, date) else str(generated_on)

    data: list[dict] = []
    for it in items:
        ident = it.get(id_key)
        if not ident:
            raise ValueError(f"item 缺少 id 欄位 {id_key!r}：{dict(it)!r}")
        row = {
            "__id": str(ident),
            "__scope": dict(it.get("scope") or {}),
            "__suggested": str(it.get(suggested_key) or "") if suggested_key else "",
            "label": str(it.get("label") or ident),
            "sublabel": str(it.get("sublabel") or ""),
            "body": str(it.get("body") or ""),
            "url": str(it.get("url") or ""),
        }
        for f in filt:
            row[f["key"]] = str(it.get(f["key"]) or "")
        data.append(row)

    # toolbar
    controls = ['<input type="search" id="q" placeholder="搜尋">']
    for f in filt:
        opts = sorted({r[f["key"]] for r in data if r[f["key"]]})
        controls.append(
            f'<select id="f_{_h(f["key"])}"><option value="">全部 {_h(f["label"])}</option>'
            + "".join(f'<option value="{_h(o)}">{_h(o)}</option>' for o in opts)
            + "</select>")
    controls.append(
        '<select id="st"><option value="">全部狀態</option>'
        '<option value="undecided">未決定</option>'
        '<option value="decided">已決定</option></select>')
    sortable = [f for f in filt if f["sort"]]
    if sortable:
        controls.append(
            '<select id="so"><option value="">預設順序</option>'
            + "".join(f'<option value="{_h(f["key"])}">{_h(f["label"])} ↓</option>'
                      for f in sortable)
            + "</select>")
    if allow_bulk_adopt and suggested_key:
        controls.append('<button onclick="bulkAdopt()">採用可見項的建議</button>')

    listeners = ["q", "st"] + [f'f_{f["key"]}' for f in filt] + (["so"] if sortable else [])
    bulk_note = ("虛線框＝建議值。" if suggested_key else "")
    bulk_note += ("" if allow_bulk_adopt else "本頁刻意不提供批次套用，每條都要你親自看過。")

    return (
        '<!doctype html>\n<html lang="zh-Hant"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_h(title)}</title>\n<style>{_CSS}</style></head><body>\n"
        f'<div class="wrap">\n<h1>{_h(title)}</h1>\n'
        f'<div class="sub">{_h(gen_on)} · 標記後按 Export 匯出 decisions JSON，'
        f"再交給 applier 套用。{_h(bulk_note)}"
        "選擇存在<b>本機瀏覽器</b>的 localStorage——換機器或換瀏覽器不會帶著走，"
        "請在標記的那台匯出。</div>\n"
        f'<div class="bar">{"".join(controls)}</div>\n'
        '<div class="stats" id="stats"></div>\n<div id="list"></div>\n</div>\n'
        '<div class="foot"><span id="progress"></span>'
        '<button class="primary" onclick="doExport()">Export decisions JSON</button></div>\n'
        "<script>\n"
        f"var DATA={_json_for_script(data)};\n"
        f"var VOCAB={_json_for_script(vocab)};\n"
        f"var FILTERS={_json_for_script(filt)};\n"
        f"var KIND={_json_for_script(kind)};\n"
        f"var GENERATED_ON={_json_for_script(gen_on)};\n"
        f"var KEY={_json_for_script(f'decision_ui:{kind}:{gen_on}')};\n"
        f"{_JS}{_BULK_JS if allow_bulk_adopt and suggested_key else ''}\n"
        + "".join(f'$("{lid}").addEventListener("input",render);\n' for lid in listeners)
        + "render();\n</script></body></html>\n"
    )


def _h(s: Any) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))
