"""Vector stamp silhouettes and safe text regions, in appearance coordinates."""
import math
import pymupdf as fitz

SHAPES=('rectangle','rounded','oval','circle','seal','badge','ribbon','hexagon','burst','tag','ticket')


BORDERS=('None','Solid','Double','Dashed')


def dimensions(shape,width,fontsize,page_height,details=False):
    if shape in ('circle','seal'):
        return min(width,page_height),min(width,page_height)
    ratio={'oval':0.48,'badge':0.8,'ribbon':0.48,'hexagon':0.42,'burst':0.62,'tag':0.36,
           'ticket':0.34}.get(shape,1/3.8)
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
    if shape=='hexagon':
        return fitz.Rect(width*0.15,height*0.17,width*0.85,height*0.83)
    if shape=='burst':
        return fitz.Rect(width*0.2,height*0.27,width*0.8,height*0.73)
    if shape=='tag':
        # Leave room for the pointed end and its eyelet.
        return fitz.Rect(width*0.24,height*0.16,width*0.94,height*0.84)
    if shape=='ticket':
        return fitz.Rect(width*0.13,height*0.16,width*0.87,height*0.84)
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
    elif shape=='hexagon':
        x0,y0,x1,y1=rect
        w,h=rect.width,rect.height
        cut=min(w*.12,h*.5)
        points=[(x0+cut,y0),(x1-cut,y0),(x1,y0+h/2),(x1-cut,y1),(x0+cut,y1),(x0,y0+h/2)]
        path.draw_polyline(points+[points[0]])
    elif shape=='burst':
        # A starburst: an ellipse with 28 short rays.
        cx,cy=(rect.x0+rect.x1)/2,(rect.y0+rect.y1)/2
        rx,ry=rect.width/2,rect.height/2
        points=[]
        for n in range(56):
            angle=math.tau*n/56-math.pi/2
            scale=1 if n%2==0 else 0.86
            points.append((cx+rx*scale*math.cos(angle),cy+ry*scale*math.sin(angle)))
        path.draw_polyline(points+[points[0]])
    elif shape=='tag':
        x0,y0,x1,y1=rect
        w,h=rect.width,rect.height
        point=min(w*.16,h*.6)
        r=min(h*.08,4)
        points=[(x0+point,y0),(x1-r,y0),(x1,y0+r),(x1,y1-r),(x1-r,y1),(x0+point,y1),(x0,y0+h/2)]
        path.draw_polyline(points+[points[0]])
        # Eyelet near the point.
        path.draw_circle((x0+point*.72,y0+h/2),max(1.2,min(h*.09,point*.25)))
    elif shape=='ticket':
        # Admission-ticket outline with half-round notches on both sides.
        x0,y0,x1,y1=rect
        h=rect.height
        r=min(h*.16,rect.width*.08)
        mid=y0+h/2
        steps=10
        right=[(x1-r*math.sin(math.pi*i/steps),mid-r*math.cos(math.pi*i/steps)) for i in range(steps+1)]
        left=[(x0+r*math.sin(math.pi*i/steps),mid+r*math.cos(math.pi*i/steps)) for i in range(steps+1)]
        points=[(x0,y0),(x1,y0)]+right+[(x1,y1),(x0,y1)]+left
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
