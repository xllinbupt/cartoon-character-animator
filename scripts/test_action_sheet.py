"""Behavioral checks for preservation, failures and repair reuse; no model calls."""
import copy
import tempfile
import unittest
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw
import action_sheet as atlas
import build_animation_assets as legacy
import alpha_qa


def request():
    return {"schema": "pet-action-request/v1", "pet_id": "fixture", "layout": {"columns": 2, "frame_size": [96, 96], "padding": 6, "placement": "preserve-cell"}, "background": {"mode": "alpha"}, "actions": [{"id": "idle", "name": "待机", "frames": 2, "fps": 5, "loop": True, "root_motion": "runtime", "motion": {"type": "none"}}, {"id": "wave", "name": "挥手", "frames": 2, "fps": 6, "loop": False, "next_action": "idle", "motion": {"type": "none"}}]}


def sheet(empty=False):
    image = Image.new("RGBA", (200, 200))
    draw = ImageDraw.Draw(image)
    for x,y,h in [(65,20,60),(125,20,30),(25,120,60),(125,120,60)]:
        if empty and y>100:
            continue
        # First pose crosses x=100 slightly; contour must be preserved intact.
        draw.rectangle((x,y,x+37,y+h), fill=(40,130,65,255))
    return image


class AtlasTests(unittest.TestCase):
    def test_alpha_audit_preserves_opaque_cream_and_source(self):
        image = Image.new("RGBA", (64, 64))
        ImageDraw.Draw(image).rectangle((10, 10, 54, 54), fill=(245, 240, 220, 255))
        before = image.tobytes()
        report = alpha_qa.inspect_alpha(image)
        self.assertEqual(report["warnings"], [])
        self.assertEqual(report["enclosed_low_alpha_regions"], [])
        self.assertEqual(image.tobytes(), before)

    def test_alpha_audit_flags_internal_cutout_without_filling(self):
        image = Image.new("RGBA", (64, 64))
        draw = ImageDraw.Draw(image)
        draw.rectangle((10, 10, 54, 54), fill=(40, 120, 60, 255))
        draw.rectangle((22, 22, 28, 28), fill=(240, 235, 210, 1))
        report = alpha_qa.inspect_alpha(image)
        self.assertEqual(report["enclosed_low_alpha_regions"], [{"bbox": [22, 22, 29, 29], "pixels": 49}])
        self.assertTrue(report["warnings"])
        self.assertEqual(image.getpixel((24, 24)), (240, 235, 210, 1))

    def test_alpha_audit_distinguishes_pale_fringe_from_dark_antialias(self):
        def fixture(color):
            image = Image.new("RGBA", (64, 64))
            draw = ImageDraw.Draw(image)
            draw.rectangle((10, 10, 54, 54), fill=(40, 120, 60, 255))
            draw.line((20, 55, 40, 55), fill=(*color, 180))
            return image
        self.assertGreater(alpha_qa.inspect_alpha(fixture((240, 240, 230)))["pale_edge_pixels"], 4)
        self.assertEqual(alpha_qa.inspect_alpha(fixture((40, 50, 30)))["pale_edge_pixels"], 0)

    def test_processing_defect_is_excluded_from_generation_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "sheet.png"
            sheet().save(source)
            out = root / "bundle"
            atlas.build(request(), source, out)
            atlas.review(out, "idle", "rejected", "local key removed pale belly", "processing")
            atlas.review(out, "wave", "rejected", "wrong hand pose", "animation")
            result = atlas.repair(request(), atlas.read(out / "manifest.json"), root / "repair")
            self.assertEqual(result["processing_actions"], ["idle"])
            self.assertEqual([a["id"] for a in atlas.read(root / "repair/request.json")["actions"]], ["wave"])
            self.assertEqual(result["model_calls"], 0)

    def test_contour_and_scale(self):
        out=atlas.extract(atlas.validate(request()),sheet())
        idle=out["idle"]
        self.assertFalse(idle["errors"])
        self.assertEqual(idle["metrics"][0]["source_bbox"][2],103)
        first,second=[m["output_bbox"] for m in idle["metrics"]]
        self.assertGreater(first[3]-first[1],(second[3]-second[1])*1.7)
        self.assertAlmostEqual(idle["metrics"][0]["scale"],idle["metrics"][1]["scale"])

    def test_missing_row_is_failure(self):
        out=atlas.extract(request(),sheet(True))
        self.assertTrue(out["wave"]["errors"])
        self.assertEqual(out["wave"]["frames"],[])

    def test_chroma_preserves_white_and_green(self):
        image=Image.new("RGB",(10,10),(255,0,255))
        image.putpixel((4,4),(255,255,255))
        image.putpixel((5,4),(0,160,0))
        keyed=atlas.matte(image,{"mode":"chroma","key":"#FF00FF"})
        self.assertEqual(keyed.getpixel((0,0)),(0,0,0,0))
        self.assertEqual(keyed.getpixel((4,4)),(255,255,255,255))
        self.assertEqual(keyed.getpixel((5,4)),(0,160,0,255))

    def test_fake_transparency_and_double_motion_refused(self):
        with self.assertRaises(ValueError):
            atlas.matte(Image.new("RGB",(10,10),"white"),{"mode":"alpha"})
        r=request();r["actions"][0].update(root_motion="frames",motion={"type":"arc","height":20,"duration_ms":1000})
        with self.assertRaises(ValueError):atlas.validate(r)

    def test_repair_keeps_approved_action_and_json_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/"sheet.png";sheet().save(source)
            first=root/"v1";atlas.build(request(),source,first)
            alpha_before = atlas.read(first/"alpha-qa.json")["actions"]["idle"]
            self.assertTrue((first/"qa/idle-alpha-source.png").exists())
            self.assertTrue((first/"qa/idle-contact-dark.png").exists())
            self.assertTrue((first/"qa/idle-contact-light.png").exists())
            self.assertEqual((first/"source"/(atlas.read(first/"qa-report.json")["source_sha256"][:12]+".png")).read_bytes(), source.read_bytes())
            atlas.review(first,"idle","approved","fixture review")
            before=(first/"frames/idle/f01.png").read_bytes()
            r=request();r["actions"]=[r["actions"][1]]
            cropped=sheet().crop((0,100,200,200));cropped.save(root/"repair.png")
            second=root/"v2";atlas.build(r,root/"repair.png",second,first)
            manifest=atlas.read(second/"manifest.json")
            self.assertEqual(atlas.read(second/"alpha-qa.json")["actions"]["idle"], alpha_before)
            self.assertEqual(manifest["actions"]["idle"]["status"],"approved")
            self.assertEqual(before,(second/"frames/idle/f01.png").read_bytes())
            wave=atlas.read(second/"actions/wave.json")
            self.assertEqual(wave["frames"][0]["y"],manifest["actions"]["wave"]["frames"][0]["y"])
            for f in wave["frames"]:
                self.assertTrue((second/"actions"/f["png"]).is_file())

    def test_failed_repair_preserves_prior_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/"sheet.png";sheet().save(source)
            first=root/"v1";atlas.build(request(),source,first)
            r=request();r["actions"]=[r["actions"][1]]
            Image.new("RGBA",(200,100)).save(root/"empty.png")
            second=root/"v2";atlas.build(r,root/"empty.png",second,first)
            result=atlas.read(second/"manifest.json")["actions"]["wave"]
            self.assertEqual(len(result["frames"]),2)
            self.assertTrue(result["last_repair_failed"])

    def test_exports_and_one_shot_loop_semantics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/"sheet.png";sheet().save(source)
            out=root/"bundle";atlas.build(request(),source,out)
            for action in ("idle", "wave"):
                for folder,suffix in (("gif",".gif"),("apng",".png"),("webp",".webp")):
                    with Image.open(out/folder/(action+suffix)) as media:
                        media.load()
                        if folder=="gif" and action=="wave":
                            self.assertNotIn("loop",media.info)
                        else:
                            self.assertEqual(media.info["loop"],0 if action=="idle" else 1)
            self.assertIn('id="fallback"',(out/"preview.html").read_text())
            self.assertNotIn('__FIRST_FRAME__',(out/"preview.html").read_text())

    def test_original_cli_and_transparent_subject_colors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            image=Image.new("RGBA",(128,64))
            draw=ImageDraw.Draw(image)
            for offset in (0,64):
                draw.rectangle((offset+15,10,offset+44,49),fill=(0,180,0,255))
                draw.rectangle((offset+22,20,offset+36,35),fill=(255,255,255,255))
            image.save(root/"source.png")
            r={"canvas":[64,64],"target_body_height":40,"sheets":[{"path":"source.png","cols":2,"rows":1,"gutter":0,"pad":0,"cells":{"a":[0,0],"b":[1,0]}}],"sequences":[
                {"name":"idle","type":"breathe","sprite":"a","frames":6,"duration":80},
                {"name":"wave","type":"cycle","sprites":["a","b"],"duration":80},
                {"name":"hop","type":"jump","sprite":"a","frames":6,"height":12,"duration":80},
                {"name":"glide","type":"smooth_locomotion","sprite":"a","frames":6,"duration":80}]}
            (root/"manifest.json").write_text(json.dumps(r))
            output=root/"output"
            subprocess.run([sys.executable,str(Path(legacy.__file__)),"--manifest",str(root/"manifest.json"),"--out",str(output)],check=True,capture_output=True,text=True)
            with Image.open(output/"sprites/a.png") as sprite:
                rgb=list(atlas.pixels(sprite))
                self.assertIn((0,180,0,255),rgb)
                self.assertIn((255,255,255,255),rgb)
            for sequence in r["sequences"]:
                for folder,suffix in (("gif",".gif"),("apng",".png"),("webp",".webp")):
                    with Image.open(output/folder/(sequence["name"]+suffix)) as media:media.load()
            self.assertTrue((output/"animation_preview.html").exists())


if __name__=="__main__":unittest.main()
