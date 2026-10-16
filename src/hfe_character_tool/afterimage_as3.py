"""AS3 source of the runtime afterimage module compiled into the target SWF.

Rules for this source (enforced by ``afterimage_patch``):

- ``//`` comments only. FFDec's AS3 parser reads a slash-star comment as a regex
  literal.
- Spaced ``for`` headers (``i < xs.length``). FFDec 21.1 mis-compiles the compact
  form into a read of ``lengthxs``.
- The two ``//@@...@@`` lines are replaced per target before compiling, because the
  Eyja fork's timestop/porter fields exist on some targets only.
"""

from __future__ import annotations

MODULE_CLASS = "HfeAfterimage"
CONFIG_PREFIX = "HFEAI1|"
TS_GATE_MARKER = "//@@TS_GATE@@"
HFP_ROUTE_MARKER = "//@@HFP_ROUTE@@"

AS3_SOURCE = r"""package Game
{
    import Data.Global;
    import Data.Spt;
    import flash.display.BitmapData;
    import flash.geom.ColorTransform;
    import flash.geom.Rectangle;

    public final class HfeAfterimage
    {
        // Rewritten in place by the exporter; format role:d1,d2,...:damage:items;...
        public static const CONFIG:String = "@@CONFIG@@";
        public static const ALPHA:Number = 0.4;
        public static const MAX_DELAY:int = 90;
        public static const PAD:Number = 80;
        public static var roles:Array = null;

        public static function parse():void
        {
            roles = [];
            var body:String = CONFIG.substr(7);
            if (body.length == 0) return;
            var parts:Array = body.split(";");
            var i:int;
            var k:int;
            for (i = 0; i < parts.length; ++i)
            {
                var fields:Array = String(parts[i]).split(":");
                if (fields.length < 4) continue;
                var raw:Array = String(fields[1]).split(",");
                var delays:Array = [];
                var size:int = 1;
                for (k = 0; k < raw.length; ++k)
                {
                    var delay:int = int(raw[k]);
                    if (delay < 1 || delay > MAX_DELAY || delays.indexOf(delay) >= 0) continue;
                    delays.push(delay);
                    if (delay > size) size = delay;
                }
                if (delays.length == 0) continue;
                roles.push({id:String(fields[0]), delays:delays, size:size,
                    damage:fields[2] == "1", items:fields[3] == "1"});
            }
        }

        public static function config(o:*):*
        {
            if (roles == null) parse();
            if (o == null || o.spt == null) return null;
            for (var i:int = 0; i < roles.length; ++i)
            {
                var role:* = roles[i];
                var spt:* = Global.sptHash[role.id];
                if (spt != null && o.spt == spt) return role;
            }
            return null;
        }

        public static function live(o:*):Boolean
        {
            return o != null && o.world != null && o.spt != null &&
                !o.deleted && !o.die && o.hp > 0 && !o.recy;
        }

        public static function owner(o:*):Boolean
        {
            return live(o) && o.hfeAiEcho == null && o.con >= -1 &&
                o.id != "dummy" && o.id != "dummyhorse" && config(o) != null;
        }

        public static function active(w:*, team:int):Boolean
        {
            return !w.playingDrama; //@@TS_GATE@@
        }

        public static function state(w:*):*
        {
            if (w.hfeAiState == null)
                w.hfeAiState = {bg:w.bg, owners:[], drawn:[], scratch:null};
            return w.hfeAiState;
        }

        public static function reset(w:*):void
        {
            if (w.hfeAiState == null) return;
            var s:* = w.hfeAiState;
            for (var i:int = 0; i < s.owners.length; ++i) clearEcho(s.owners[i]);
            if (s.scratch != null) s.scratch.dispose();
            w.hfeAiState = null;
        }

        public static function tick(w:*):void
        {
            if (w.bg == null) return;
            var s:* = state(w);
            if (s.bg != w.bg)
            {
                reset(w);
                s = state(w);
            }
            var units:Array = w.obj.concat();
            var i:int;
            var d:*;
            for (i = s.owners.length - 1; i >= 0; --i)
            {
                d = s.owners[i];
                if (!owner(d) || d.hfeAiHist == null || d.hfeAiHist.world != w ||
                    d.hfeAiHist.team != d.team || !d.inObj)
                {
                    clearEcho(d);
                    s.owners.splice(i, 1);
                }
            }
            for (i = 0; i < units.length; ++i)
            {
                d = units[i];
                if (owner(d) && d.inObj && active(w, d.team)) echoTick(w, s, d, units);
            }
            settle(w, s);
        }

        public static function releaseGrab(e:*):void
        {
            if (e == null || e.cat == null) return;
            var v:* = e.cat;
            if (v.cau == e)
            {
                v.cau = null;
                if (live(v))
                {
                    v.ToActionFall(C._fall1);
                    v.vy = 10;
                }
            }
            e.cat = null;
        }

        public static function clearEcho(d:*):void
        {
            if (d == null || d.hfeAiHist == null) return;
            var slots:Array = d.hfeAiHist.slots;
            for (var k:int = 0; k < slots.length; ++k)
            {
                var e:* = slots[k].echo;
                if (e != null)
                {
                    releaseGrab(e);
                    e.deleted = true;
                    // The echo was never registered with the world lists.
                    e.ReleaseAll();
                    slots[k].echo = null;
                }
            }
            d.hfeAiHist = null;
        }

        public static function snapshot(h:*):*
        {
            return {x:h.x, y:h.y, z:h.z, vx:h.vx, vy:h.vy, vz:h.vz, face:h.facing,
                agi:h.agi, ati:h.ati, ai:h.ai, fi:h.fi, dur:h.dur, o:h.o,
                land:h.landY, str:h.str, lv:h.lv, brk:h.brk, brkin:h.brkin,
                up:h.pU, down:h.pD, left:h.pL, right:h.pR, key:h.keyS1,
                hadCat:h.cat != null, app:h.app, shots:h.hfeAiShots};
        }

        public static function echoTick(w:*, s:*, d:*, units:Array):void
        {
            var cfg:* = config(d);
            var hist:* = d.hfeAiHist;
            var k:int;
            if (hist != null && (hist.world != w || hist.cfg != cfg))
            {
                clearEcho(d);
                hist = null;
            }
            if (hist == null)
            {
                hist = {ring:new Array(cfg.size), cursor:0, team:d.team, world:w, cfg:cfg,
                    slots:[]};
                for (k = 0; k < cfg.delays.length; ++k)
                    hist.slots.push({delay:cfg.delays[k], echo:null, lastFi:-1, lastDur:-1,
                        lastAgi:-1, lastAi:-1, lastAti:-1});
                d.hfeAiHist = hist;
                if (s.owners.indexOf(d) < 0) s.owners.push(d);
            }
            var now:* = snapshot(d);
            d.hfeAiShots = null;
            var n:int = hist.ring.length;
            // Read every delayed sample before this tick's sample overwrites the oldest one.
            var olds:Array = [];
            for (k = 0; k < hist.slots.length; ++k)
                olds.push(hist.ring[(hist.cursor - hist.slots[k].delay + n) % n]);
            hist.ring[hist.cursor] = now;
            hist.cursor = (hist.cursor + 1) % n;
            for (k = 0; k < hist.slots.length; ++k)
            {
                if (olds[k] != null) replay(w, d, cfg, units, hist.slots[k], olds[k], k);
            }
        }

        public static function replay(w:*, d:*, cfg:*, units:Array, sl:*, old:*, k:int):void
        {
            var e:* = sl.echo;
            if (e == null)
            {
                e = new ObjH("hfe_ai_" + d.id + "_" + k, w, d.spt, "", d.team, -3, -1);
                e.SetA(C._nor, C._basic, C._stand);
                e.SetApp(d.app);
                e.hfeAiHideBars();
                e.hfeAiEcho = d;
                e.hitable = false;
                e.repelable = false;
                e.invC = 0;
                e.mapC = -1;
                e.ml = null;
                e.storyO = null;
                e.bt_troop = null;
                e.hp = e.mHp = d.mHp;
                e.mp = e.mMp = d.mMp;
                e.st = e.mSt = d.mSt;
                sl.echo = e;
            }
            if (!old.hadCat && e.cat != null && d.spt.frame[old.fi].cat == null) releaseGrab(e);
            var changed:Boolean = sl.lastFi != old.fi || sl.lastDur > old.dur ||
                sl.lastAgi != old.agi || sl.lastAti != old.ati || sl.lastAi != old.ai;
            e.agi = old.agi;
            e.ati = old.ati;
            e.ai = old.ai;
            e.ag = e.spt.actionGroup[old.agi];
            e.a = e.ag.action[old.ati][old.ai];
            e.fi = old.fi;
            e.f = e.spt.frame[old.fi];
            e.dur = old.dur;
            e.x = old.x;
            e.y = old.y;
            e.z = old.z;
            e.vx = old.vx;
            e.vy = old.vy;
            e.vz = old.vz;
            e.facing = old.face;
            e.o = old.o;
            e.landY = old.land;
            e.str = old.str;
            e.lv = old.lv;
            e.brk = old.brk;
            e.brkin = old.brkin;
            e.team = e.aTeam = d.team;
            e.aOwner = d;
            e.bt_team = d.bt_team;
            e.pU = old.up;
            e.pD = old.down;
            e.pL = old.left;
            e.pR = old.right;
            e.keyS1 = old.key;
            if (e.app != old.app) e.SetApp(old.app);
            e.stun = e.selfStun = 0;
            if (changed && (e.f.newAttack || e.f.attack.length == 0 || sl.lastAi != old.ai ||
                sl.lastAti != old.ati || sl.lastAgi != old.agi))
            {
                e.aedList = [];
                e.aedListT = [];
                e.aEnd = false;
            }
            sl.lastFi = old.fi;
            sl.lastDur = old.dur;
            sl.lastAi = old.ai;
            sl.lastAti = old.ati;
            sl.lastAgi = old.agi;
            bounds(e);
            if (cfg.items && old.shots != null)
            {
                for (var p:int = 0; p < old.shots.length; ++p) playShot(w, d, old.shots[p]);
            }
            if (!cfg.damage)
            {
                if (e.cat != null) releaseGrab(e);
                return;
            }
            if (e.cat != null)
            {
                if (!live(e.cat) || e.cat.cau != e) releaseGrab(e);
                else if (e.f.cat != null)
                {
                    var held:* = e.cat;
                    e.PosCatchee();
                    if (held.useAb && held.aOwner == e) held.aOwner = d;
                    bounds(held);
                }
                else releaseGrab(e);
            }
            for (var i:int = 0; i < units.length; ++i)
            {
                var v:* = units[i];
                if (v == d || !live(v) || v.f == null) continue;
                if (v.cau != null && v.cau != e) continue;
                w.hfeAiCheck(v, e);
                if (e.cat == null && Math.abs(v.x - e.x) <= 180 && Math.abs(v.z - e.z) <= 60 &&
                    Math.abs(v.y - e.y) <= 150 && v is ObjH)
                    w.hfeAiCatch(e, v);
            }
        }

        public static function bounds(h:*):void
        {
            h.ix = int(h.x);
            h.iy = int(h.y);
            h.iz = int(h.z);
            h.facing0 = h.facing;
            h.foot = h.f.onGround ? 0 : h.f.footY;
            h.b = h.ix + (h.facing == 0 ? h.f.bx1 : -h.f.bx2);
            h.bx2 = h.ix + (h.facing == 0 ? h.f.bx2 : -h.f.bx1);
            h.by1 = h.iy + h.f.by1;
            h.by2 = h.iy + h.f.by2;
            h.bz1 = h.iz + h.f.bz1;
            h.bz2 = h.iz + h.f.bz2;
            h.brkin0 = h.brkin;
        }

        public static function settle(w:*, s:*):void
        {
            for (var i:int = 0; i < s.owners.length; ++i)
            {
                var d:* = s.owners[i];
                if (!owner(d) || d.hfeAiHist == null || !active(w, d.team)) continue;
                if (!d.hfeAiHist.cfg.damage) continue;
                var slots:Array = d.hfeAiHist.slots;
                for (var k:int = 0; k < slots.length; ++k)
                {
                    var e:* = slots[k].echo;
                    if (e == null) continue;
                    if (e.aList.length > 0)
                    {
                        dedupHits(e);
                        if (e.aList.length > 0) w.hfeAiApply(e);
                        e.aList = [];
                    }
                    e.stun = e.selfStun = 0;
                    if (e.cat != null && e.f.cat != null) e.PosCatchee();
                }
            }
        }

        public static function dedupHits(e:*):void
        {
            var seen:Object = {};
            var out:Array = [];
            for (var i:int = 0; i < e.aList.length; ++i)
            {
                var hit:* = e.aList[i];
                if (hit.o == null || hit.o.hfeAiEcho != null) continue;
                var key:String = String(hit.o.id);
                if (seen[key]) continue;
                seen[key] = true;
                out.push(hit);
            }
            e.aList = out;
        }

        public static function recordShot(h:*, item:*):void
        {
            if (item == null || item == h.hfeAiLastShot || !owner(h)) return;
            var cfg:* = config(h);
            if (!cfg.items) return;
            // The emitter loop also increments after a skipped entry; record each item once.
            h.hfeAiLastShot = item;
            if (h.hfeAiShots == null) h.hfeAiShots = [];
            h.hfeAiShots.push({agi:item.agi, ai:item.ai, spt:item.spt, x:item.x, y:item.y, z:item.z,
                facing:item.facing, vx:item.vx, vy:item.vy, vz:item.vz, o:item.o,
                ref:item.ab.ref, useAb:item.useAb, str:item.str});
        }

        public static function playShot(w:*, d:*, p:*):void
        {
            //@@HFP_ROUTE@@
            var item:* = w.AddObjI(p.agi, p.ai, int(p.x), int(p.z), int(p.y), p.facing, d.team);
            if (item == null) return;
            if (item.spt != p.spt)
            {
                item.spt = p.spt;
                item.SetA(p.agi, C._basic, p.ai);
                item.setAGProperties();
            }
            item.o = p.o;
            item.vx = p.vx;
            item.vy = p.vy;
            item.vz = item.vz0 = p.vz;
            item.aOwner = d;
            item.aTeam = d.team;
            item.str = p.str;
            if (item.f.body.length > 0 && p.useAb)
            {
                var b:* = item.f.body[0];
                item.ab.ref = p.ref;
                item.ab.x1 = b.x1;
                item.ab.x2 = b.x2;
                item.ab.y1 = b.y1;
                item.ab.y2 = b.y2;
                item.ab.z1 = b.z1;
                item.ab.z2 = b.z2;
                item.useAb = true;
            }
        }

        public static function beginDraw(w:*):void
        {
            var s:* = w.hfeAiState;
            if (s == null) return;
            s.drawn = [];
            if (s.scratch != null) s.scratch.fillRect(s.scratch.rect, 0);
        }

        public static function region(w:*, e:*, bmp:BitmapData, half:Boolean):Rectangle
        {
            var f:* = e.f;
            var x1:Number;
            var x2:Number;
            if (e.facing == 0)
            {
                x1 = e.x + f.dbx1;
                x2 = e.x + f.dbx2;
            }
            else
            {
                x1 = e.x - f.dbx2;
                x2 = e.x - f.dbx1;
            }
            var y1:Number = Math.min(e.z + e.y + f.dby1, e.z + e.landY);
            var y2:Number = Math.max(e.z + e.y + f.dby2, e.z + e.landY);
            // ObjH.Draw maps world to screen as (v - scl) * scale; DrawH halves it.
            var k:Number = half ? w.scale / 2 : w.scale;
            if (isNaN(x1 + x2 + y1 + y2 + k) || !(k > 0)) return bmp.rect.clone();
            var left:Number = Math.max(0, Math.floor((x1 - PAD - w.sclX) * k));
            var right:Number = Math.min(bmp.width, Math.ceil((x2 + PAD - w.sclX) * k));
            var top:Number = Math.max(0, Math.floor((y1 - PAD - w.sclY) * k));
            var bottom:Number = Math.min(bmp.height, Math.ceil((y2 + PAD - w.sclY) * k));
            if (right <= left || bottom <= top) return null;
            return new Rectangle(left, top, right - left, bottom - top);
        }

        public static function drawOne(w:*, e:*, bmp:BitmapData, half:Boolean):void
        {
            var s:* = w.hfeAiState;
            s.drawn.push(e);
            var r:Rectangle = region(w, e, bmp, half);
            if (r == null) return;
            if (s.scratch == null || s.scratch.width != bmp.width || s.scratch.height != bmp.height)
            {
                if (s.scratch != null) s.scratch.dispose();
                s.scratch = new BitmapData(bmp.width, bmp.height, true, 0);
            }
            s.scratch.fillRect(r, 0);
            e.hfeAiHideBars();
            if (half) e.DrawH(s.scratch);
            else e.Draw(s.scratch);
            bmp.draw(s.scratch, null, new ColorTransform(1, 1, 1, ALPHA), null, r);
            s.scratch.fillRect(r, 0);
        }

        public static function drawBefore(o:*, bmp:BitmapData, half:Boolean):void
        {
            var w:* = o.world;
            if (w == null || w.hfeAiState == null) return;
            drawUntil(w, bmp, half, o.z);
        }

        public static function drawUntil(w:*, bmp:BitmapData, half:Boolean, z:Number):void
        {
            var s:* = w.hfeAiState;
            if (s == null) return;
            var sorted:Array = [];
            for (var i:int = 0; i < s.owners.length; ++i)
            {
                var d:* = s.owners[i];
                if (!owner(d) || d.hfeAiHist == null) continue;
                var slots:Array = d.hfeAiHist.slots;
                for (var k:int = 0; k < slots.length; ++k)
                {
                    var e:* = slots[k].echo;
                    if (e != null && e.f != null && s.drawn.indexOf(e) < 0 && e.z <= z)
                        sorted.push({echo:e, z:e.z});
                }
            }
            sorted.sortOn("z", Array.NUMERIC);
            for (i = 0; i < sorted.length; ++i) drawOne(w, sorted[i].echo, bmp, half);
        }

        public static function endDraw(w:*, bmp:BitmapData, half:Boolean):void
        {
            drawUntil(w, bmp, half, Number.MAX_VALUE);
        }
    }
}
"""
