"""Vector certificate signature cards, also used for on-page placement previews."""
from datetime import datetime
from html import escape
import pymupdf as fitz

TEMPLATES=('Modern','Minimal','Formal')


def template_pdf(name='Certificate holder', reason='', template='Modern', date=None, rotation=0):
    if template not in TEMPLATES:raise ValueError('Unknown signature template.')
    date=date or datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %Z')
    with fitz.open() as doc:
        page=doc.new_page(width=360,height=112)
        navy=(.09,.18,.29);teal=(.04,.49,.47)
        if template=='Modern':
            page.draw_rect(page.rect,color=(.82,.87,.89),fill=(.97,.985,.985),width=.7)
            page.draw_rect(fitz.Rect(0,0,5,112),color=None,fill=teal)
            page.draw_rect(fitz.Rect(5,0,360,26),color=None,fill=navy)
            page.insert_text((18,17),'DIGITAL SIGNATURE',fontsize=9,fontname='hebo',color=(1,1,1))
            left=18;top=33
        elif template=='Minimal':
            page.draw_line((12,106),(348,106),color=navy,width=1)
            page.insert_text((12,17),'DIGITALLY SIGNED',fontsize=8,fontname='hebo',color=teal)
            left=12;top=28
        else:
            page.draw_rect(fitz.Rect(1,1,359,111),color=navy,fill=(1,1,1),width=1)
            page.draw_circle((38,52),23,color=navy,width=1.5)
            page.draw_circle((38,52),19,color=navy,width=.5)
            page.insert_text((27,56),'DS',fontsize=15,fontname='hebo',color=navy)
            page.insert_text((78,19),'CERTIFICATE SIGNATURE',fontsize=9,fontname='hebo',color=navy)
            left=78;top=29
        content=(f'<div class="name">{escape(name)}</div>'
                 f'<div class="detail">Signed {escape(date)}</div>'
                 +(f'<div class="detail">{escape(reason)}</div>' if reason.strip() else '')
                 +'<div class="caption">Signed with a digital certificate</div>')
        page.insert_htmlbox(fitz.Rect(left,top,346,103),content,
            css='* {font-family: sans-serif; margin: 0;} .name {font-size: 17pt; font-weight: bold; color: #172e4a;} '
                '.detail {font-size: 9pt; color: #40536a; margin-top: 4pt;} '
                '.caption {font-size: 7pt; color: #657789; margin-top: 5pt;}',scale_low=0)
        data=doc.tobytes()
        if not rotation:return data
        with fitz.open() as rotated:
            width,height=(112,360) if rotation%180 else (360,112)
            target=rotated.new_page(width=width,height=height)
            target.show_pdf_page(target.rect,doc,0,rotate=rotation)
            return rotated.tobytes()


def template_png(**options):
    with fitz.open(stream=template_pdf(**options),filetype='pdf') as doc:
        return doc[0].get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False).tobytes('png')


def pdf_box(pdf_bytes,placement,password=''):
    """Convert canvas (unrotated, crop-relative) bounds to native PDF bounds."""
    with fitz.open(stream=pdf_bytes,filetype='pdf') as doc:
        if doc.needs_pass and not doc.authenticate(password):raise ValueError('The PDF password is incorrect.')
        number=placement['page']
        if not isinstance(number,int) or not 0<=number<doc.page_count:raise ValueError('Invalid signature page.')
        page=doc[number]
        rotation=page.rotation
        page.set_rotation(0)
        rect=fitz.Rect(placement['rect'])
        if rect.is_empty or rect.is_infinite or not rect in page.rect:raise ValueError('Place the signature inside the page.')
        width,height=(rect.height,rect.width) if rotation%180 else (rect.width,rect.height)
        if width<80 or height<24:raise ValueError('The signature area is too small. Draw a larger rectangle.')
        return tuple(rect*~page.transformation_matrix)
