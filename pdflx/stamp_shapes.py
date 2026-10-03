"""Vector stamp silhouettes and safe text regions, in appearance coordinates."""
import math
import pymupdf as fitz

SHAPES=('rectangle','rounded','oval','circle','seal','badge','ribbon')


BORDERS=('None','Solid','Double','Dashed')


def dimensions(shape,width,fontsize,page_height,details=False):
    if shape in ('circle','seal'):
        return min(width,page_height),min(width,page_height)
    ratio={'oval':0.48,'badge':0.8,'ribbon':0.48}.get(shape,1/3.8)
    minimum=fontsize*1.5+12
    if details:
        # Room for the smaller details line under the main text.
        ratio*=1.3
        minimum+=fontsize*0.7
    return width,min(page_height,max(width*ratio,minimum))


def split_region(region,details):
    """Main-text and details-line boxes inside a text region."""
    if not details:
        return region,None
    cut=region.y0+region.height*0.62
    return (fitz.Rect(region.x0,region.y0,region.x1,cut),
            fitz.Rect(region.x0+region.width*0.04,cut+region.height*0.04,region.x1-region.width*0.04,region.y1))


def text_region(shape,width,height):
    if shape in ('circle','seal'):
        return fitz.Rect(width*0.15,height*0.28,width*0.85,height*0.72)
    if shape=='oval':
        return fitz.Rect(width*0.13,height*0.2,width*0.87,height*0.8)
    if shape=='badge':
        return fitz.Rect(width*0.13,height*0.15,width*0.87,height*0.62)
    if shape=='ribbon':
        return fitz.Rect(width*0.21,height*0.25,width*0.79,height*0.75)
    padding=min(8,width*0.15,height*0.2)
    return fitz.Rect(padding,padding,width-padding,height-padding)


def _outline(path,shape,width,height,inset):
    rect=fitz.Rect(inset,inset,width-inset,height-inset)
    if shape=='rectangle':
        path.draw_rect(rect)
    elif shape in ('oval','circle'):
        path.draw_oval(rect)
    elif shape=='rounded':
        x0,y0,x1,y1=rect
        r=min(rect.height*0.25,rect.width*0.1)
        k=0.5522847498*r
        path.draw_line((x0+r,y0),(x1-r,y0))
        path.draw_bezier((x1-r,y0),(x1-r+k,y0),(x1,y0+r-k),(x1,y0+r))
        path.draw_line((x1,y0+r),(x1,y1-r))
        path.draw_bezier((x1,y1-r),(x1,y1-r+k),(x1-r+k,y1),(x1-r,y1))
        path.draw_line((x1-r,y1),(x0+r,y1))
        path.draw_bezier((x0+r,y1),(x0+r-k,y1),(x0,y1-r+k),(x0,y1-r))
        path.draw_line((x0,y1-r),(x0,y0+r))
        path.draw_bezier((x0,y0+r),(x0,y0+r-k),(x0+r-k,y0),(x0+r,y0))
    elif shape=='seal':
        center=(width/2,height/2)
        radius=min(width,height)/2-inset
        points=[]
        for n in range(64):
            angle=math.tau*n/64-math.pi/2
            r=radius*(1 if n%2==0 else 0.91)
            points.append((center[0]+r*math.cos(angle),center[1]+r*math.sin(angle)))
        path.draw_polyline(points+[points[0]])
    elif shape=='badge':
        x0,y0,x1,y1=rect
        w,h=rect.width,rect.height
        points=[(x0+w*.08,y0),(x1-w*.08,y0),(x1,y0+h*.53),
                (x0+w*.8,y0+h*.8),(x0+w*.5,y1),(x0+w*.2,y0+h*.8),
                (x0,y0+h*.53)]
        path.draw_polyline(points+[points[0]])
    else:
        x0,y0,x1,y1=rect
        w,h=rect.width,rect.height
        points=[(x0,y0+h*.2),(x0+w*.18,y0+h*.2),(x0+w*.18,y0),
                (x0+w*.82,y0),(x0+w*.82,y0+h*.2),(x1,y0+h*.2),
                (x0+w*.9,y0+h*.6),(x1,y1),(x0+w*.82,y1),
                (x0+w*.82,y0+h*.8),(x0+w*.18,y0+h*.8),
                (x0+w*.18,y1),(x0,y1),(x0+w*.1,y0+h*.6)]
        path.draw_polyline(points+[points[0]])


def draw_border(page,shape,width,height,color,border,fill=False):
    inset=min(2,min(width,height)*.08)
    stroke=min(2,min(width,height)*.05)
    if fill:
        path=page.new_shape()
        _outline(path,shape,width,height,inset)
        path.finish(color=None,fill=color,fill_opacity=0.12,width=0,closePath=True)
        path.commit()
    if border=='None':
        return
    path=page.new_shape()
    _outline(path,shape,width,height,inset)
    path.finish(color=color,width=stroke,closePath=True,dashes='[5 3] 0' if border=='Dashed' else None)
    if border=='Double':
        # A thinner inner rule, like a rubber stamp's double frame.
        gap=min(4,min(width,height)*.06)
        _outline(path,shape,width,height,inset+stroke+gap)
        path.finish(color=color,width=stroke*0.6,closePath=True)
    path.commit()
    if border=='Double':
        path=page.new_shape()
        if shape=='seal':
            margin=min(width,height)*.12
            path.draw_oval(fitz.Rect(margin,margin,width-margin,height-margin))
        else:
            _outline(path,shape,width,height,min(5,min(width,height)*.16))
        path.finish(color=color,width=1,closePath=True)
        path.commit()
