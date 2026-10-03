"""Reader selections follow PDF glyphs rather than a rectangular word clip."""
import pymupdf as fitz


def characters(page):
    result=[]
    line_number=0
    for block in page.get_text('rawdict',flags=0,sort=True)['blocks']:
        for line in block.get('lines',[]):
            for span in line.get('spans',[]):
                for char in span.get('chars',[]):
                    quad=fitz.recover_char_quad(line['dir'],span,char)*page.rotation_matrix
                    result.append((char['c'],quad,line_number))
            line_number+=1
    return result


def nearest(items,point):
    point=fitz.Point(point)
    def distance(item):
        rect=item[1].rect
        dx=max(rect.x0-point.x,0,point.x-rect.x1)
        dy=max(rect.y0-point.y,0,point.y-rect.y1)
        return (dx*dx+dy*dy,abs(point-(rect.tl+rect.br)/2))
    return min(range(len(items)),key=lambda index:distance(items[index]))


def select(items,start=None,end=None):
    if not items:
        return '',None,[]
    first,last=(0,len(items)-1) if start is None else sorted((nearest(items,start),nearest(items,end)))
    selected=items[first:last+1]
    text=[]
    previous=selected[0][2]
    bounds=fitz.Rect()
    quads=[]
    for char,quad,line in selected:
        if line!=previous:
            text.append('\n')
        text.append(char)
        bounds|=quad.rect
        quads.append(quad)
        previous=line
    return ''.join(text),tuple(bounds),quads
