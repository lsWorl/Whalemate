"""原创动画离线补间回归：合成图检查定位、透明边缘和运动，绝不联网。"""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

AVAILABLE=all(importlib.util.find_spec(x) is not None for x in ('numpy','PIL'))
if AVAILABLE:
    import numpy as np
    from PIL import Image,ImageDraw
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
    from animation_inbetween import extract_keys,optical_flow,interpolate
    from pose_bridge import pose_interpolate


@unittest.skipUnless(AVAILABLE,'离线美术工具需要已有 Pillow / NumPy；运行桌宠不需要')
class AnimationBuildTests(unittest.TestCase):
    def rectangle(self,offset=0):
        image=Image.new('RGBA',(48,48));ImageDraw.Draw(image).rectangle((12+offset,14,28+offset,34),fill=(245,30,20,255));return image

    def test_interpolation_preserves_endpoints_and_alpha_without_dark_halo(self):
        a,b=self.rectangle(),self.rectangle(3);f=optical_flow(a,b);g=optical_flow(b,a)
        self.assertTrue(np.array_equal(np.asarray(interpolate(a,b,0,f,g)),np.asarray(a)))
        self.assertTrue(np.array_equal(np.asarray(interpolate(a,b,1,f,g)),np.asarray(b)))
        mid=np.asarray(interpolate(a,b,.5,f,g))
        self.assertEqual(int(mid[0,0,3]),0)
        visible=mid[:,:,3]>10
        self.assertGreater(int(mid[:,:,0][visible].min()),235)
        yy,xx=np.mgrid[:48,:48];weights=mid[:,:,3].astype(float)
        self.assertAlmostEqual(float((xx*weights).sum()/weights.sum()),21.5,delta=.4)
        self.assertTrue(all(np.isfinite(x).all() for x in (*f,*g)))

    def test_identical_keys_remain_still_and_crisp(self):
        a=self.rectangle();flow=optical_flow(a,a)
        self.assertLess(float(np.abs(flow[0]).max()),1e-6)
        self.assertLess(float(np.abs(flow[1]).max()),1e-6)
        mid=np.asarray(interpolate(a,a,.5,flow,flow))
        self.assertTrue(np.array_equal(mid,np.asarray(a)))

    def test_disconnected_dust_is_removed_and_six_feet_are_aligned(self):
        with tempfile.TemporaryDirectory() as directory:
            image=Image.new('RGBA',(330,240));draw=ImageDraw.Draw(image)
            for i in range(6):
                x=i%3*110+35+i%2;y=i//3*120+20+i%3
                draw.rectangle((x,y,x+35,y+69),fill=(50,80,240,255))
                draw.point((i%3*110+10,i//3*120+10),fill=(50,80,240,255))
            path=Path(directory)/'sheet.png';image.save(path)
            frames,report=extract_keys(path,96)
            self.assertEqual(len(frames),6)
            feet=[frame.getbbox()[3] for frame in frames]
            self.assertLessEqual(max(feet)-min(feet),1)
            for frame in frames:
                box=frame.getbbox();self.assertGreater(box[0],2);self.assertGreater(box[1],2)
                self.assertLess(box[2],94);self.assertLess(box[3],94)

    def test_pose_bridge_moves_one_opaque_face_to_intermediate_height(self):
        def person(top):
            image=Image.new('RGBA',(128,192));draw=ImageDraw.Draw(image)
            draw.rectangle((40,top,86,top+40),fill=(250,190,170,255))
            draw.rectangle((50,top+40,76,175),fill=(60,80,220,255))
            return image
        a,b=person(20),person(70)
        middle=pose_interpolate(a,b,.5)
        color=middle.getpixel((63,65))
        self.assertGreater(color[0],235);self.assertGreater(color[3],245)
        # 原来的头部位置已移开，不应在上方再叠出第二张脸。
        self.assertEqual(middle.getpixel((63,25))[3],0)
        self.assertTrue(np.array_equal(np.asarray(pose_interpolate(a,b,0)),np.asarray(a)))
        self.assertTrue(np.array_equal(np.asarray(pose_interpolate(a,b,1)),np.asarray(b)))

    def test_transition_preview_can_be_built_in_clean_workspace(self):
        import build_pose_transitions as build
        with tempfile.TemporaryDirectory() as directory,patch.object(build,'ROOT',Path(directory)):
            build.gif_preview('tiny',[self.rectangle(),self.rectangle(1)])
            path=Path(directory)/'output/transitions/tiny.gif'
            self.assertTrue(path.read_bytes().startswith(b'GIF'))

    def test_source_character_cut_by_outer_canvas_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            image=Image.new('RGBA',(330,240));draw=ImageDraw.Draw(image)
            for i in range(6):
                x=0 if i==0 else i%3*110+35;y=i//3*120+20
                draw.rectangle((x,y,x+35,y+69),fill=(50,80,240,255))
            path=Path(directory)/'cut.png';image.save(path)
            with self.assertRaises(ValueError):extract_keys(path,96)


if __name__=='__main__':unittest.main()
