"""整帧行走交付契约：原画序列直接播放，不能重新引入拆肢体驱动或光流补间。"""
import hashlib
import json
from pathlib import Path
import sys
import unittest
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from build_walk_cels import ART,OUT,CELS,CYCLES,COUNT,read_sheet,BASE_ART,sequence_order

class WholeCharacterCelTests(unittest.TestCase):
    def test_every_encoded_frame_is_one_complete_painted_cel(self):
        for direction in ('left','right'):
            record=json.loads((ART/direction/'sequence.json').read_text(encoding='utf-8'))
            order=record['sequence']
            self.assertEqual(len(order),COUNT)
            self.assertEqual(order,sequence_order())
            self.assertTrue(record['no_limb_rig']);self.assertTrue(record['no_optical_flow'])
            cels=[]
            for index in range(CELS):
                path=ART/direction/f'{index:02}.png'
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),record['cel_sha256'][index])
                with Image.open(path) as im:cels.append(im.convert('RGBA').tobytes())
            self.assertEqual(len(set(cels)),CELS)
            with Image.open(OUT/f'walking_{direction}.webp') as atlas:
                for index,cel in enumerate(order):
                    frame=atlas.crop((cel%8*384,cel//8*384,cel%8*384+384,cel//8*384+384))
                    self.assertEqual(frame.convert('RGBA').tobytes(),cels[cel],(direction,index))

    def test_originals_unchanged_and_inbetweens_keep_aspect_ratios(self):
        for direction in ('left','right'):
            record=json.loads((ART/direction/'sequence.json').read_text(encoding='utf-8'))
            self.assertEqual(record['newly_painted_cels'],17)
            for item in record['registration']:
                left,top,right,bottom=item['bounds']
                if item['kind']=='new_inbetween':
                    width,height=item['source_size'];scale=item['scale']
                    self.assertAlmostEqual(right-left,width*scale,delta=2)
                    self.assertAlmostEqual(bottom-top,height*scale,delta=2)
                else:
                    index=item['cel']//2
                    with Image.open(BASE_ART/direction/f'{index:02}.png') as old,Image.open(ART/direction/f'{index*2:02}.png') as new:
                        self.assertEqual(old.convert('RGBA').tobytes(),new.convert('RGBA').tobytes())
                self.assertEqual(bottom,326)
                self.assertLessEqual(abs((left+right)/2-192),1)
                self.assertGreaterEqual(min(left,top,384-right,384-bottom),39)

    def test_metadata_records_both_original_whole_frame_sources(self):
        source=json.loads((OUT/'SOURCE.json').read_text(encoding='utf-8'))
        self.assertEqual(source['walking_method'],'whole_character_painted_cels')
        self.assertNotIn('walking_rig_sources',source)
        for direction in ('left','right'):
            item=source['walking_cel_sources']['walking_'+direction]
            self.assertTrue(item['whole_frame_only'])
            self.assertEqual(item['painted_cels'],34)
            self.assertEqual(len(item['sheets']),1)
            self.assertEqual(len(read_sheet(ART/f'{direction}-sheet.png',rows=6)),18)

if __name__=='__main__':unittest.main()
