import os,sys,time,json,tempfile,shutil,re,base64
os.environ["QT_QPA_PLATFORM"]="windows"
from PySide6.QtCore import Qt, QByteArray, QDataStream, QIODevice
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication
QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app=QApplication([])
from pathlib import Path
from widgetdevtool.main_window import MainWindow, State
from widgetdevtool.console_log import BRIDGE_SOURCE
OUT={}
def pump(s):
    t=time.time()
    while time.time()-t<s: app.processEvents(); time.sleep(0.01)
def wait(f,t=40):
    e=time.time()+t
    while time.time()<e:
        app.processEvents()
        if f(): return True
        time.sleep(0.02)
    return False
def js(rt,code):
    box={}; rt.page.runJavaScript(code,lambda v: box.setdefault("v",v)); wait(lambda:"v" in box,10); return box.get("v")
def jj(rt,expr): 
    v=js(rt,f"JSON.stringify({expr})"); 
    try: return json.loads(v)
    except Exception: return v
def shot(w,name):
    p=os.path.join(tempfile.gettempdir(),name); w.grab().save(p); return p
SRC=Path(r"D:\Widgets\WidgetsXE\RSS Feed Reader\widget\rssfeedreader")
INST=Path(os.path.expandvars(r"%APPDATA%\Corsair\CUE5\html_widgets\com\shocksim\rssfeedreader"))
data=Path(tempfile.mkdtemp(prefix="wdt_e1_data_"))
w=MainWindow(data_dir=data); w.resize(1350,950); w.show(); pump(1)
# ---- S1 library
n=w.library_panel.set_library(r"D:\Widgets\WidgetsXE")
xe=next((p for p in w.profiles if "XENEON" in p.name.upper()),None)
OUT["S1"]={"widgets_found":n,"xeneon_profile":xe.id if xe else None}
if xe: w.select_device(xe.id)
ok=w.library_panel.select_widget(SRC); ent=w.library_panel.selected_entry()
OUT["S1"].update({"selected":ok,"entry":str(getattr(ent,'name',None)),"group":str(getattr(ent,'group',None))})
OUT["S1"]["open"]=w.open_library_widget(SRC); rt=w.active; wait(lambda: rt.state is State.LOADED,40); pump(2)
OUT["S1"]["state"]=rt.state.name; OUT["S1"]["window_title"]=w.windowTitle()
# ---- S2 metadata + settings
ed=w.settings_panel.editors
OUT["S2"]={"editors":len(ed),"keys":sorted(ed),"unsupported":{k:e.reason for k,e in ed.items() if e.reason},"instance_keys":sorted(rt.instance.data),
 "type_id":rt.instance.type_id,"name":rt.instance.name,"source_version":json.loads((SRC/"manifest.json").read_text())["version"],"groups":[g.title() for g in w.settings_panel.group_boxes]}
# compare with the key set stored by iCUE 5.52.93 (installed 2.0.4)
icue_keys=['accentColor1','accentColor2','accentColor3','accentColor4','accentColor5','backgroundColor','backgroundMedia','bgBrightness','feedUrl1','feedUrl2','feedUrl3','feedUrl4','feedUrl5','fontFamily','glassBlur','refreshMinutes','textColor','transparency','widgetPersonalizationCustomStyleEnabled','widgetPersonalizationCustomStylePresent']
OUT["S2"]["vs_icue_keys"]={"only_in_icue":sorted(set(icue_keys)-set(rt.instance.data)),"only_in_tool":sorted(set(rt.instance.data)-set(icue_keys))}
# ---- decode iCUE values (read only)
t=open(os.path.expandvars(r"%APPDATA%\Corsair\CUE5\dashlcd\storage"),encoding="utf-8").read()
i=t.find("com.shocksim.rssfeedreader"); blk=t[i:i+6000]
dec={}
for m in re.finditer(r"<key>(\w+)</key>\s*<value>([^<]*)</value>",blk):
    k,b=m.groups()
    ba=QByteArray.fromBase64(QByteArray(b.encode()),QByteArray.Base64Option.Base64UrlEncoding|QByteArray.Base64Option.OmitTrailingEquals)
    ds=QDataStream(ba); ds.setVersion(QDataStream.Version.Qt_6_5)
    try: v=ds.readQVariant()
    except Exception as e: v=f"<decode error {e}>"
    dec[k]=v
def fmt(v):
    from PySide6.QtGui import QColor
    if isinstance(v,QColor): return v.name()
    return v if not isinstance(v,str) or not v.startswith("http") else "<url>"
OUT["S2"]["icue_decoded"]={k:(type(v).__name__,fmt(v)) for k,v in dec.items()}
OUT["S2"]["tool_defaults_types"]={k:(type(v).__name__,v if not (isinstance(v,str) and v.startswith("http")) else "<url>") for k,v in rt.instance.data.items()}
# ---- S3 personalization
pers={}
pers["has_custom_style_switch"]=w.settings_panel.custom_style_check is not None
pers["custom_style_off_textColor_page"]=js(rt,"typeof textColor!=='undefined'?textColor:null")
w.set_custom_style(True); pump(0.5)
pers["custom_style_on_textColor_page"]=js(rt,"typeof textColor!=='undefined'?textColor:null")
pers["instance_textColor_raw"]=rt.instance.data.get("textColor")
w.set_custom_style(False); pump(0.3)
OUT["S3"]=pers
# ---- S4 device/size/orientation
S4=[]
if xe:
    for size in xe.supported_sizes():
        for ori in xe.orientations():
            vp=w.select_device(xe.id,size,ori); pump(0.8)
            S4.append({"size":size,"ori":ori,"viewport":list(vp.size),"page":jj(rt,"[innerWidth,innerHeight,devicePixelRatio]"),"zoom":round(w.viewport_frame.zoom,4),"view_px":[rt.view.width(),rt.view.height()]})
    w.viewport_frame.set_scale(1.0); w.select_device(xe.id,xe.supported_sizes()[0],xe.orientations()[0]); pump(0.8)
    S4.append({"note":"scale 100%","page":jj(rt,"[innerWidth,innerHeight,devicePixelRatio]"),"zoom":w.viewport_frame.zoom})
    w.viewport_frame.set_scale(None)
OUT["S4"]=S4
OUT["S4_shots"]=shot(w,"wdt_e1_s4.png")
# ---- S7 console / lifecycle for the open instance
ents=rt.console.entries
OUT["S7"]={"counts":{lv:len([e for e in ents if e.level==lv]) for lv in ("info","warning","error")},
 "widget_errors":[e.message[:150] for e in ents if e.level=="error" and e.source!=BRIDGE_SOURCE][:10],
 "widget_warnings":[e.message[:150] for e in ents if e.level=="warning" and e.source!=BRIDGE_SOURCE][:6],
 "bridge_notices":[e.message[:130] for e in ents if e.source==BRIDGE_SOURCE][:10],
 "ls_keys":jj(rt,"Object.keys(localStorage)"),"uniqueId":js(rt,"uniqueId"),"initialized":js(rt,"typeof iCUE_initialized!=='undefined'&&iCUE_initialized")}
OUT["S7"]["page_state"]=jj(rt,"({title:document.title,bodyChildren:document.body.children.length,text:document.body.innerText.slice(0,160)})")
open(os.path.join(tempfile.gettempdir(),"wdt_e1_a.json"),"w",encoding="utf-8").write(json.dumps(OUT,indent=1,default=str))
# ---- S5 live reload on a TEMP COPY (WidgetsXE untouched)
tmp=Path(tempfile.mkdtemp(prefix="wdt_e1_copy_"))/"rss"; shutil.copytree(SRC,tmp)
w.select_device(None)
w.load_folder(tmp); c=w.active; wait(lambda:c.state is State.LOADED,40); pump(1.5)
w._on_setting_committed("refreshMinutes",7.0); pump(0.3)
before=jj(c,"[typeof refreshMinutes!=='undefined'?refreshMinutes:null]")
css=tmp/"styles"/"rssfeedreader.css"; css.write_text(css.read_text(encoding="utf-8")+"\nbody{outline:3px solid #f0f !important}\n",encoding="utf-8")
ok=wait(lambda:c.reload_count>=1 and not c.reloading,25); pump(1)
S5={"css_reload":ok,"reload_count":c.reload_count,"outline":js(c,"getComputedStyle(document.body).outlineStyle"),"user_value_kept":jj(c,"[refreshMinutes]"),"before":before,"same_instance_id":True}
idx=tmp/"index.html"; txt=idx.read_text(encoding="utf-8"); idx.write_text(txt.replace("</head>","<script>syntax error here (</script></head>",1),encoding="utf-8")
pump(2.5); S5["after_js_syntax_error"]={"reload_count":c.reload_count,"state":c.state.name,"widget_errors":[e.message[:100] for e in c.console.entries if e.level=="error" and e.source!=BRIDGE_SOURCE][-2:]}
idx.write_text(txt,encoding="utf-8"); wait(lambda:c.reload_count>=3 and not c.reloading,25); pump(1)
S5["after_fix"]={"reload_count":c.reload_count,"state":c.state.name,"user_value_kept":jj(c,"[refreshMinutes]")}
OUT["S5"]=S5
# ---- S6 multi-instance
w.load_folder(SRC); a=w.active; wait(lambda:a.state is State.LOADED,40)
w._on_setting_committed("feedUrl1","https://example.invalid/a.xml"); w._on_setting_committed("refreshMinutes",11.0)
w.new_instance(); b=w.active; wait(lambda:b.state is State.LOADED,40); w._on_setting_committed("feedUrl1","https://example.invalid/b.xml"); w._on_setting_committed("refreshMinutes",22.0); pump(3)
S6={"ids_differ":a.instance.id!=b.instance.id,"a":jj(a,"[feedUrl1,refreshMinutes,uniqueId]"),"b":jj(b,"[feedUrl1,refreshMinutes,uniqueId]"),"a_ls":jj(a,"Object.keys(localStorage)"),"b_ls":jj(b,"Object.keys(localStorage)"),"visible":[not a.view.isHidden(),not b.view.isHidden()]}
S6["a_ls_blob_keys"]=jj(a,"(()=>{const o={};for(const k of Object.keys(localStorage)){try{const j=JSON.parse(localStorage.getItem(k));o[k]=j&&typeof j==='object'?Object.keys(j).slice(0,6):typeof j}catch(e){o[k]='nonjson'}}return o})()")
js(a,"localStorage.setItem('e1_marker','A'); 1"); js(b,"localStorage.setItem('e1_marker','B'); 1")
ids=[a.instance.id,b.instance.id]; ls_before=[jj(a,"Object.keys(localStorage)"),jj(b,"Object.keys(localStorage)")]
w.shutdown(); pump(1)
w2=MainWindow(data_dir=data); w2.resize(1350,950); w2.show()
S6["restored"]=w2.restore_workspace()
for r in w2.runtimes.values(): wait(lambda r=r:r.state is State.LOADED,40)
pump(3)
rr=[w2.runtimes[i] for i in ids if i in w2.runtimes]
S6["after_restart"]=[{"marker":js(r,"localStorage.getItem('e1_marker')"),"keys":jj(r,"Object.keys(localStorage)"),"feedUrl1":js(r,"feedUrl1"),"refresh":js(r,"refreshMinutes")} for r in rr]
S6["ls_keys_before_restart"]=ls_before
OUT["S6"]=S6
OUT["shots"]=shot(w2,"wdt_e1_s6.png")
open(os.path.join(tempfile.gettempdir(),"wdt_e1_a.json"),"w",encoding="utf-8").write(json.dumps(OUT,indent=1,default=str))
w2.shutdown(); pump(0.5); print("DONE")