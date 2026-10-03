"""Compact Field / Appearance pages for native AcroForm settings."""
from gi.repository import Gtk,Gdk
from .form_properties import color_rgb


def add_form_settings(dialog,field=None):
    field=field or {}
    basics=dialog.grid
    grid=dialog.new_grid
    basics.set_margin_top(14)
    notebook=Gtk.Notebook(show_border=False)
    notebook.add_css_class('pdflx-tabs')
    dialog.form_notebook=notebook
    dialog.box.remove(basics)
    notebook.append_page(basics,Gtk.Label(label='Field'))
    dialog.box.prepend(notebook)
    dialog.grid=grid();dialog.row=0
    notebook.append_page(dialog.grid,Gtk.Label(label='Behaviour'))
    tooltip=dialog.entry('Help text',field.get('tooltip',''))
    multiline=dialog.check('Multiline text',field.get('multiline',False))
    readonly=dialog.check('Read-only field',field.get('readonly',False))
    editable=dialog.check('Allow custom dropdown values',bool(field.get('flags',0)&(1<<18)))
    password=dialog.check('Password (hide typed characters)',field.get('password',False))
    comb=dialog.check('Comb (one character per box; needs a character limit)',field.get('comb',False))
    multi=dialog.check('Allow multiple selections',field.get('multi_select',False))
    limit=None
    if not field:
        limit=dialog.spin('Character limit (0 = unlimited)',0,0,100000)
    by_kind={'text':[password,comb]+([limit,dialog.grid.get_child_at(0,dialog.row-1)] if limit else []),
             'list':[multi],'combo':[editable]}
    def show_for(kind):
        for key,widgets in by_kind.items():
            for widget in widgets:
                widget.set_visible(key==kind)
        multiline.set_visible(kind=='text')
    dialog.form_kind_controls=show_for
    if field:
        kind={7:'text',4:'list',3:'combo'}.get(field['type'])
        show_for(kind)
    dialog.grid=grid()
    dialog.row=0
    notebook.append_page(dialog.grid,Gtk.Label(label='Appearance'))
    fonts=['Helv','HeBo','TiRo','Cour','ZaDb']
    current=field.get('font','Helv')
    if current not in fonts:fonts.append(current)
    font=dialog.dropdown('Font',fonts);font.set_selected(fonts.index(current))
    size=dialog.spin('Font size (0 = automatic)',field.get('font_size',0),0,72)
    width=dialog.spin('Border width · pt',field.get('border_width',1),0,10,.5)
    styles=['solid','dashed','beveled','inset','underline']
    style=dialog.dropdown('Border style',['Solid','Dashed','Beveled','Inset','Underline'])
    style.set_selected(styles.index(field.get('border_style','solid')) if field.get('border_style','solid') in styles else 0)
    colors={}
    for key,label,default in (('text_color','Text color',(0,0,0)),
                              ('border_color','Border color',(.45,.45,.45)),
                              ('fill_color','Background color',(1,1,1))):
        button=Gtk.ColorButton(title=label)
        rgba=Gdk.RGBA();rgba.red,rgba.green,rgba.blue=color_rgb(field.get(key),default);rgba.alpha=1
        button.set_rgba(rgba);dialog.field(label,button);colors[key]=button
    transparent=dialog.check('Transparent background',bool(field) and field.get('fill_color') is None)
    def read():
        result=dict(tooltip=tooltip.get_text(),multiline=multiline.get_active(),readonly=readonly.get_active(),
                    editable_choice=editable.get_active(),font=fonts[font.get_selected()],
                    font_size=size.get_value(),border_width=width.get_value(),
                    border_style=styles[style.get_selected()],password=password.get_active(),
                    comb=comb.get_active(),multi_select=multi.get_active())
        if limit is not None:result['max_length']=limit.get_value_as_int()
        for key,button in colors.items():
            rgba=button.get_rgba();result[key]=(rgba.red,rgba.green,rgba.blue)
        if transparent.get_active():result['fill_color']=None
        return result
    return read,multiline,editable
