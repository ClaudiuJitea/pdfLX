"""Real GTK workflow: new shapes, shape options, the Line/Arrow tools, and AI shape options."""
import json
from types import SimpleNamespace
from unittest.mock import Mock
import gi
gi.require_version('Gtk','4.0')
gi.require_version('Adw','1')
from gi.repository import Gtk,Adw,GLib,Gio,Gdk
import pymupdf as fitz
from pdflx.window import PdfEditorWindow
from pdflx import pdf_handler
from pdflx.shape_geometry import PRESETS, outline

app=Adw.Application(application_id='org.pdflx.TestShapes',flags=Gio.ApplicationFlags.NON_UNIQUE)
app.register()
window=PdfEditorWindow(application=app)
window.doc,_=pdf_handler.create_new_pdf()
window.view_mode=False
window._load_page(0)
window.present()
loop=GLib.MainLoop()
errors=[]
tools=window.shape_tools

def offset():
    return (max(0,(window.pdf_view.get_width()-window.current_pdf_page_width)/2),
            max(0,(window.pdf_view.get_height()-window.current_pdf_page_height)/2))

def drag(x0,y0,x1,y1,state=Gdk.ModifierType(0)):
    ox,oy=offset();z=window.zoom_level
    gesture=SimpleNamespace(set_state=Mock(),get_current_event_state=lambda:state)
    window.on_drag_begin(gesture,ox+x0*z,oy+y0*z)
    window.on_drag_update(gesture,(x1-x0)*z,(y1-y0)*z)
    window.on_drag_end(gesture,(x1-x0)*z,(y1-y0)*z)

def paths(rect):
    return [k for k,r in window.doc[0].get_bboxlog() if fitz.Rect(r).intersects(rect)]

def every_preset():
    assert len(tools.menu_buttons)==len(PRESETS)+2
    x,y=40,40
    for key,_label,_icon,_extra in PRESETS:
        tools.menu_buttons[key].emit('clicked')
        assert window.tool_mode in ('add_shape','add_rectangle','add_ellipse','add_checkmark','add_cross'),window.tool_mode
        before=len(window.editable_shapes)
        drag(x,y,x+60,y+50)
        assert len(window.editable_shapes)==before+1,key
        shape=window.editable_shapes[-1]
        assert paths(fitz.Rect(x,y,x+60,y+50)),key
        x+=80
        if x>480:x,y=40,y+90
    star=[s for s in window.editable_shapes if s.shape_type=='star'][0]
    assert len(outline(star)[0][1])==10
    rounded=[s for s in window.editable_shapes if getattr(s,'corner_radius',0)>0 and s.shape_type=='rectangle'][0]
    assert any(seg[0]=='C' for seg in outline(rounded)[0][1])

def options_on_selection():
    shape=[s for s in window.editable_shapes if s.shape_type=='polygon'][0]
    window.on_tool_selected(None,'select')
    window.selected_shape=shape
    window._update_shape_format_controls(shape)
    assert tools.sides.get_visible() is False or tools.sides.row_widgets[1].get_visible()
    assert not tools.radius.row_widgets[1].get_visible(),'corner radius hidden for polygons'
    tools.sides.set_value(8)
    assert shape.sides==8
    tools.opacity.set_value(40)
    assert abs(shape.opacity-0.4)<1e-6
    tools.dash.set_selected(1)
    assert shape.dash=='dashed'
    window.lookup_action('undo').activate(None)
    assert shape.dash=='solid' and abs(shape.opacity-0.4)<1e-6
    # Outline width 0 means no outline: only a fill is painted.
    window.shape_stroke_width_spin.set_value(0)
    assert shape.stroke_width==0

def line_tool():
    tools.menu_buttons['arrow_line'].emit('clicked')
    assert window.tool_mode=='add_line' and tools.arrow_end.get_active()
    before=len(window.editable_strokes)
    drag(60,600,300,640,Gdk.ModifierType.SHIFT_MASK)
    assert len(window.editable_strokes)==before+1
    line=window.editable_strokes[-1]
    (ax,ay),(bx,by)=line.points
    angle=abs(__import__('math').degrees(__import__('math').atan2(by-ay,bx-ax)))
    assert abs(angle-round(angle/15)*15)<0.01,'Shift snaps to 15 degrees'
    assert line.arrow_end and line.tool_type=='line'
    log=paths(fitz.Rect(line.bbox))
    assert 'stroke-path' in log and 'fill-path' in log,'arrowhead is drawn'
    drag(60,700,61,700)
    assert len(window.editable_strokes)==before+1,'a click without dragging adds no line'
    window.on_tool_selected(None,'select')
    window.selected_stroke=line
    window._update_stroke_format_controls(line)
    tools.arrow_start.set_active(True)
    assert line.arrow_start

def ai_shapes():
    bar=window.ai_bar
    text,_=bar.tools.run('add_elements',{'page':1,'elements':[
        {'type':'rectangle','x':40,'y':720,'width':120,'height':40,'fill':'#b7dfc9','corner_radius':12},
        {'type':'polygon','sides':6,'x':180,'y':720,'width':40,'height':40,'stroke':'#000000','dash':'dotted'},
        {'type':'star','x':240,'y':720,'width':40,'height':40,'fill':'#ffcc00','opacity':0.5},
        {'type':'line','x1':300,'y1':740,'x2':400,'y2':740,'arrow_end':True,'dash':'dashed'},
    ]})
    data=json.loads(text)
    assert 'error' not in data,data
    listing=json.loads(bar.tools.run('list_elements',{'page':1})[0])['elements']
    rect=[e for e in listing if e['type']=='rectangle' and e.get('corner_radius')==12][0]
    assert rect['fill']=='#b7dfc9' and rect['stroke'] is None
    assert any(e['type']=='polygon' and e.get('dash')=='dotted' for e in listing)
    assert any(e['type']=='line' and e.get('arrow_end') and e.get('dash')=='dashed' for e in listing)
    text,_=bar.tools.run('edit_elements',{'page':1,'edits':[{'id':rect['id'],'corner_radius':4,'opacity':0.8}]})
    assert 'error' not in text,text

steps=[every_preset,options_on_selection,line_tool,ai_shapes]
def run():
    try:
        steps.pop(0)()
    except Exception as error:
        import traceback;traceback.print_exc();errors.append(error);steps.clear()
    if steps:return True
    loop.quit();return False
GLib.timeout_add(300,run)
loop.run()
if errors:raise SystemExit(1)
print('shapes workflow ok')
