"""Freehand pen strokes stay fast to draw and keep sensible arrowheads."""
import math
import time
import unittest

from pdflx.models import EditableStroke
from pdflx.shape_geometry import stroke_ends


class PenStrokeTests(unittest.TestCase):
    def test_long_strokes_grow_bbox_in_constant_time(self):
        stroke=EditableStroke(points=[(0,0)],stroke_color=(0,0,0),stroke_width=2,tool_type='pen',
                              page_number=0,is_new=True)
        stroke.recalculate_bbox()
        start=time.perf_counter()
        for index in range(20000):
            stroke.add_point(index*0.01,math.sin(index/50)*10)
        self.assertLess(time.perf_counter()-start,1.0)
        grown=stroke.bbox
        stroke.recalculate_bbox()
        self.assertEqual(grown,stroke.bbox)

    def test_freehand_arrow_follows_the_path_not_the_last_jitter(self):
        # The mouse stops: the last segments are tiny or repeated.
        points=[(0,0),(30,0),(30.2,0.1),(30.2,0.1)]
        _points,heads=stroke_ends(points,2,arrow_end=True)
        tip,left,right=heads[0]
        base=((left[0]+right[0])/2,(left[1]+right[1])/2)
        # The head points right along the stroke and has a real size.
        self.assertLess(base[0],tip[0]-5)
        self.assertGreater(math.dist(left,right),5)


if __name__=='__main__':
    unittest.main()
