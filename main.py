import sys, math, random, csv, os, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dataclasses import dataclass, field
import pygame
from traffic_engine import World

FPS = 60

# Modern HUD palette
BG = (10, 14, 20)
ROAD = (47, 52, 60)
ROAD_EDGE = (78, 84, 94)
LANE_MARK = (108, 114, 124)
WHITE = (244, 247, 250)
MUTED = (150, 158, 170)
PANEL = (17, 22, 30)
PANEL_2 = (23, 29, 38)
ACCENT = (55, 205, 150)
BLUE = (70, 150, 245)
ORANGE = (238, 160, 55)
RED = (235, 65, 75)
YELLOW = (245, 195, 55)
ROAD_W = 260
LANE_W = 58
CONTROLS_H = 160  # height of the bottom controls bar; playfield must stay above this
STOP_D = 215.0
CENTER_D = 330.0
EXIT_D = 650.0
CAR_GAP = 44.0
SPAWN_GAP = 58.0
MANUAL_SPAWN_INTERVAL = 0.45
YELLOW_TIME = 3.0
FIXED_GREEN = 30.0
ADAPTIVE_BASE = 10.0
ADAPTIVE_FACTOR = 2.5
ADAPTIVE_MAX = 45.0
EMERGENCY_GREEN = 20.0
AUTO_INTERVALS = {'low': 2.2, 'medium': 1.15, 'high': 0.55}

DIRS = ['N','E','S','W']

# ============================================================
# SCREENSHOT FEATURE — fully self-contained, easy to remove.
# To disable without deleting anything: set ENABLE_SCREENSHOT_FEATURE = False.
# To remove entirely: delete every block between the
# "SCREENSHOT FEATURE START" / "SCREENSHOT FEATURE END" markers
# in this file (constants here, App.__init__, App.take_screenshot,
# the key handler in loop(), the controls-legend entry, and the
# toast in panel()) plus the ", time" import above.
# ============================================================
ENABLE_SCREENSHOT_FEATURE = False
SCREENSHOT_DIR = r"C:\Users\HARISH\Downloads\Team Indolent\need2\op"
SCREENSHOT_KEY = pygame.K_p
# ============================================================
KINDS = ['Car','Taxi','Bus','Truck','Van','Bike']
KIND_SIZE = {'Car':(18,30),'Taxi':(18,30),'Bus':(24,44),'Truck':(25,40),'Van':(20,34),'Bike':(14,22)}
KIND_COLOR = {'Car':(70,150,235),'Taxi':(235,190,45),'Bus':(75,185,105),'Truck':(175,110,65),'Van':(160,100,205),'Bike':(235,125,70)}

@dataclass
class Vehicle:
    direction: str
    distance: float = 0.0
    kind: str = 'Car'
    emergency: bool = False
    waiting: float = 0.0
    entered_intersection: bool = False
    exited: bool = False
    # Lane is fixed for the full trip: one incoming lane becomes the same geometric lane outgoing.
    lane: int = 0

class App:
    def __init__(self):
        pygame.init()
        info=pygame.display.Info()
        self.windowed=(1280,800)
        self.fullscreen=True
        self.screen=pygame.display.set_mode((info.current_w,info.current_h),pygame.FULLSCREEN)
        pygame.display.set_caption('AI-Adaptive Traffic Signal Control')
        self.clock=pygame.time.Clock()
        self.world=World(seed=42)
        self.world.load_fluid_data()
        self.running=True
        self.font=pygame.font.SysFont('segoeui',21)
        self.small=pygame.font.SysFont('segoeui',16)
        self.tiny=pygame.font.SysFont('segoeui',13)
        self.title=pygame.font.SysFont('segoeui',32,bold=True)
        self.big=pygame.font.SysFont('segoeui',42,bold=True)
        self.badge=pygame.font.SysFont('segoeui',24,bold=True)
        self.metric=pygame.font.SysFont('segoeui',28,bold=True)
        # --- SCREENSHOT FEATURE START ---
        self.last_screenshot_msg=''
        self.last_screenshot_time=0.0
        # --- SCREENSHOT FEATURE END ---

    # --- SCREENSHOT FEATURE START ---
    def take_screenshot(self):
        """Saves the current frame exactly as shown, to SCREENSHOT_DIR."""
        try:
            os.makedirs(SCREENSHOT_DIR, exist_ok=True)
            fname = f"screenshot_{time.strftime('%Y%m%d_%H%M%S')}.png"
            path = os.path.join(SCREENSHOT_DIR, fname)
            pygame.image.save(self.screen, path)
            self.last_screenshot_msg = f"Saved {fname}"
        except Exception as e:
            self.last_screenshot_msg = f"Screenshot failed: {e}"
        self.last_screenshot_time = time.time()
    # --- SCREENSHOT FEATURE END ---

    def toggle_fullscreen(self):
        self.fullscreen=not self.fullscreen
        if self.fullscreen:
            info=pygame.display.Info(); self.screen=pygame.display.set_mode((info.current_w,info.current_h),pygame.FULLSCREEN)
        else:
            self.screen=pygame.display.set_mode(self.windowed)

    def playfield(self):
        """Returns (W, H, cx, cy, play_h): the intersection is centered within
        the area ABOVE the bottom controls bar, so nothing (signals included)
        can ever be drawn underneath it, regardless of window size."""
        W,H=self.screen.get_size()
        play_h = max(H-CONTROLS_H, 400)
        cx, cy = W//2, play_h//2
        return W,H,cx,cy,play_h

    def road_coords(self,v):
        _,_,cx,cy,_=self.playfield()
        # Fixed lane geometry. A vehicle keeps the SAME lane across the intersection.
        # lane 0 = northbound/eastbound style lane; lane 1 = opposite direction.
        if v.direction=='N':
            return cx-LANE_W//2, cy-310+v.distance
        if v.direction=='S':
            return cx+LANE_W//2, cy+310-v.distance
        if v.direction=='E':
            return cx+310-v.distance, cy+LANE_W//2
        return cx-310+v.distance, cy-LANE_W//2

    def draw_vehicle(self,v):
        x,y=self.road_coords(v); s=self.screen
        if v.emergency:
            pygame.draw.ellipse(s,(4,6,9),(int(x-20),int(y+15),40,10))
            pygame.draw.circle(s,(255,255,255),(int(x),int(y)),24)
            pygame.draw.circle(s,(220,35,35),(int(x),int(y)),19)
            pygame.draw.circle(s,(255,220,45),(int(x),int(y)),8)
            pygame.draw.circle(s,(255,255,255),(int(x),int(y)),19,2)
            return
        w,h=KIND_SIZE[v.kind]
        if v.direction in 'EW': w,h=h,w
        pygame.draw.ellipse(s,(4,6,9),(int(x-w/2),int(y+h/2-3),w,8))
        r=pygame.Rect(int(x-w/2),int(y-h/2),w,h)
        pygame.draw.rect(s,KIND_COLOR[v.kind],r,border_radius=7)
        base=KIND_COLOR[v.kind]
        hl_col=tuple(min(255,c+40) for c in base)
        hl_w,hl_h=max(2,r.w-6),max(2,r.h//3)
        pygame.draw.rect(s,hl_col,(r.x+3,r.y+3,hl_w,hl_h),border_radius=5)
        pygame.draw.rect(s,(230,230,230),r,2,border_radius=7)

    def arrow(self, start, end):
        s=self.screen; pygame.draw.line(s,(155,160,165),start,end,3)
        ang=math.atan2(end[1]-start[1],end[0]-start[0])
        pts=[end,(end[0]-12*math.cos(ang-.5),end[1]-12*math.sin(ang-.5)),(end[0]-12*math.cos(ang+.5),end[1]-12*math.sin(ang+.5))]
        pygame.draw.polygon(s,(155,160,165),pts)

    def rounded_panel(self, rect, fill=PANEL, border=ROAD_EDGE, radius=16, width=1, shadow=True):
        rect = pygame.Rect(rect)
        if shadow:
            sh = pygame.Surface((rect.w+16, rect.h+16), pygame.SRCALPHA)
            pygame.draw.rect(sh,(0,0,0,90),(8,10,rect.w,rect.h),border_radius=radius)
            self.screen.blit(sh,(rect.x-8,rect.y-6))
        pygame.draw.rect(self.screen, fill, rect, border_radius=radius)
        if border:
            pygame.draw.rect(self.screen, border, rect, width, border_radius=radius)

    def dashed_line(self, p1, p2, color, width=3, dash=14, gap=10):
        x1,y1=p1; x2,y2=p2
        dist=math.hypot(x2-x1,y2-y1)
        if dist==0: return
        dx,dy=(x2-x1)/dist,(y2-y1)/dist
        n=int(dist//(dash+gap))+1
        for i in range(n):
            start=i*(dash+gap); end=min(start+dash,dist)
            pygame.draw.line(self.screen,color,(x1+dx*start,y1+dy*start),(x1+dx*end,y1+dy*end),width)

    def draw_signal(self, x, y, active, vertical=True, label=None):
        """A real traffic-light housing (red/yellow/green stack) instead of a
        single flat dot, so the active phase is unmistakable at any size."""
        s=self.screen
        w,h=(30,76) if vertical else (76,30)
        rect=pygame.Rect(int(x-w/2),int(y-h/2),w,h)
        sh=pygame.Surface((w+14,h+14),pygame.SRCALPHA)
        pygame.draw.rect(sh,(0,0,0,110),(7,9,w,h),border_radius=10)
        s.blit(sh,(rect.x-7,rect.y-5))
        pygame.draw.rect(s,(21,24,30),rect,border_radius=10)
        pygame.draw.rect(s,(68,74,84),rect,2,border_radius=10)
        dim={'red':(72,32,34),'yellow':(74,62,26),'green':(26,58,44)}
        lit={'red':RED,'yellow':YELLOW,'green':ACCENT}
        for i,name in enumerate(('red','yellow','green')):
            lx,ly=(x, rect.top+15+i*23) if vertical else (rect.left+15+i*23, y)
            col=lit[name] if name==active else dim[name]
            if name==active:
                glow=pygame.Surface((34,34),pygame.SRCALPHA)
                pygame.draw.circle(glow,(*col,70),(17,17),17)
                s.blit(glow,(int(lx-17),int(ly-17)))
                pygame.draw.circle(s,col,(int(lx),int(ly)),10)
                pygame.draw.circle(s,(255,255,255),(int(lx),int(ly)),10,1)
            else:
                pygame.draw.circle(s,col,(int(lx),int(ly)),8)
        if label:
            lab=self.tiny.render(label,True,MUTED)
            lx = rect.centerx - lab.get_width()//2
            ly = rect.bottom+4 if vertical else rect.centery-lab.get_height()//2
            lx = lx if vertical else rect.right+6
            s.blit(lab,(lx,ly))

    def text(self, value, pos, font=None, color=WHITE):
        self.screen.blit((font or self.font).render(str(value), True, color), pos)

    def pill(self, label, x, y, fill, width=None):
        font = self.small
        surf = font.render(label, True, WHITE)
        w = width or surf.get_width() + 24
        h = 30
        pygame.draw.rect(self.screen, fill, (x, y, w, h), border_radius=15)
        self.screen.blit(surf, (x + (w-surf.get_width())//2, y + 6))
        return w

    def draw_road(self):
        s=self.screen; W,H,cx,cy,play_h=self.playfield()
        s.fill(BG)
        pygame.draw.rect(s,ROAD,(cx-ROAD_W//2,0,ROAD_W,H))
        pygame.draw.rect(s,ROAD,(0,cy-ROAD_W//2,W,ROAD_W))
        # crisp edges so the road reads clearly against the background
        pygame.draw.line(s,ROAD_EDGE,(cx-ROAD_W//2,0),(cx-ROAD_W//2,H),2)
        pygame.draw.line(s,ROAD_EDGE,(cx+ROAD_W//2,0),(cx+ROAD_W//2,H),2)
        pygame.draw.line(s,ROAD_EDGE,(0,cy-ROAD_W//2),(W,cy-ROAD_W//2),2)
        pygame.draw.line(s,ROAD_EDGE,(0,cy+ROAD_W//2),(W,cy+ROAD_W//2),2)
        # lane boundaries: center is NOT shared by opposite traffic; it separates the two lanes.
        self.dashed_line((cx,0),(cx,cy-ROAD_W//2),LANE_MARK,3,16,12)
        self.dashed_line((cx,cy+ROAD_W//2),(cx,H),LANE_MARK,3,16,12)
        self.dashed_line((0,cy),(cx-ROAD_W//2,cy),LANE_MARK,3,16,12)
        self.dashed_line((cx+ROAD_W//2,cy),(W,cy),LANE_MARK,3,16,12)
        # stop lines
        pygame.draw.line(s,(245,245,245),(cx-ROAD_W//2,cy-STOP_D),(cx,cy-STOP_D),5)
        pygame.draw.line(s,(245,245,245),(cx,cy+STOP_D),(cx+ROAD_W//2,cy+STOP_D),5)
        pygame.draw.line(s,(245,245,245),(cx+STOP_D,cy),(cx+STOP_D,cy+ROAD_W//2),5)
        pygame.draw.line(s,(245,245,245),(cx-STOP_D,cy-ROAD_W//2),(cx-STOP_D,cy),5)
        self.arrow((cx-LANE_W//2,90),(cx-LANE_W//2,35))
        self.arrow((cx+LANE_W//2,H-90),(cx+LANE_W//2,H-35))
        self.arrow((W-90,cy+LANE_W//2),(W-35,cy+LANE_W//2))
        self.arrow((90,cy-LANE_W//2),(35,cy-LANE_W//2))

        c=self.world.controller
        # sig_offset is clamped to the playfield's own half-height, so every
        # signal housing (including South) always stays fully inside the
        # visible area and can never end up hidden behind the controls bar.
        sig_offset=min(STOP_D+45, cy-70)
        positions={
            'N':(cx-100, cy-sig_offset),
            'S':(cx+100, cy+sig_offset),
            'E':(cx+sig_offset, cy+100),
            'W':(cx-sig_offset, cy-100),
        }
        for d,(x,y) in positions.items():
            vertical = d in ('N','S')
            if c.emergency_active:
                active = 'green' if d==c.emergency_dir else 'red'
            elif c.state=='green' and d in c.group():
                active='green'
            elif c.state=='yellow' and d in c.group():
                active='yellow'
            else:
                active='red'
            self.draw_signal(x,y,active,vertical,label=d)

    def panel(self):
        s=self.screen; W,H=s.get_size(); c=self.world.controller

        # Top-left status card
        mode = 'AI ADAPTIVE' if c.mode=='adaptive' else 'FIXED TIMER'
        mode_col = BLUE if c.mode=='adaptive' else ORANGE
        self.rounded_panel((24,22,390,78), PANEL)
        pygame.draw.circle(s, mode_col, (52,61), 8)
        self.text(mode, (70,43), self.badge, WHITE)
        self.text('TRAFFIC CONTROL SIMULATION', (70,73), self.tiny, MUTED)

        # Emergency banner
        if c.emergency_active:
            self.rounded_panel((W//2-255,22,510,78), (55,20,24), RED, 16, 2)
            pygame.draw.circle(s, RED, (W//2-218,61), 9)
            self.text('EMERGENCY PRIORITY', (W//2-198,43), self.badge, WHITE)
            self.text(f'DIRECTION {c.emergency_dir}', (W//2-198,72), self.tiny, (255,180,180))

        # Right-side live dashboard
        p=pygame.Rect(W-415,118,385,365)
        self.rounded_panel(p, PANEL, ROAD_EDGE, 18, 1)

        phase='NORTH / SOUTH' if c.phase==0 else 'EAST / WEST'
        phase_dirs = c.group()
        signal_col = ACCENT if c.state=='green' else (YELLOW if c.state=='yellow' else RED)
        traffic_label = 'FLUID VERY HIGH' if self.world.fluid_enabled else (
            self.world.auto_level.upper() if self.world.auto else 'MANUAL')

        self.text('LIVE CONTROL', (W-388,140), self.title, WHITE)
        self.text('CURRENT SIGNAL', (W-388,181), self.tiny, MUTED)
        self.text(c.state.upper(), (W-388,198), self.metric, signal_col)

        # Phase + countdown
        self.text(phase, (W-235,184), self.small, WHITE)
        self.text(f'{max(0,c.remaining):.1f}s', (W-112,181), self.metric, WHITE)

        # Countdown bar
        duration = max(0.1, c.duration(self.world.demand()))
        ratio = max(0.0, min(1.0, c.remaining / duration))
        pygame.draw.rect(s, (39,45,55), (W-388,235,330,8), border_radius=4)
        pygame.draw.rect(s, signal_col, (W-388,235,int(330*ratio),8), border_radius=4)

        # Direction cards
        q=self.world.counts()
        dirs = [('N','NORTH'),('E','EAST'),('S','SOUTH'),('W','WEST')]
        for i,(d,label) in enumerate(dirs):
            x = W-388 + (i%2)*170
            y = 260 + (i//2)*55
            active = d in phase_dirs and c.state == 'green'
            card_fill = PANEL_2 if not active else (24,45,40)
            self.rounded_panel((x,y,155,45), card_fill, ACCENT if active else None, 10, 1)
            self.text(label, (x+12,y+7), self.tiny, MUTED)
            self.text(str(q[d]), (x+112,y+5), self.metric, WHITE)

        # Bottom metrics strip
        strip_y = H-235
        self.rounded_panel((24,strip_y,W-48,58), PANEL, ROAD_EDGE, 14, 1)
        grade, avg_delay = self.world.congestion_level()
        grade_col = {'A':ACCENT,'B':ACCENT,'C':YELLOW,'D':ORANGE,'E':RED,'F':RED}.get(grade, WHITE)
        metrics = [
            ('THROUGHPUT', self.world.total_passed, WHITE, 190),
            ('MAX QUEUE', self.world.max_queue, WHITE, 190),
            ('CONGESTION', f'{grade} · {avg_delay:.0f}s delay', grade_col, 230),
            ('SPEED', f'{self.world.sim_speed:.0f}x', WHITE, 190),
            ('TRAFFIC', traffic_label, WHITE, 0),
        ]
        x=44
        for label,val,col,step in metrics:
            self.text(label, (x,strip_y+9), self.tiny, MUTED)
            self.text(val, (x,strip_y+27), self.font, col)
            x += step

        # Controls / keyboard legend
        h=CONTROLS_H
        y=H-h
        pygame.draw.rect(s,(8,11,16),(0,y,W,h))
        pygame.draw.line(s,ACCENT,(0,y),(W,y),2)
        self.text('CONTROLS', (28,y+16), self.small, WHITE)

        controls = [
            ('A', 'Adaptive'), ('F', 'Fixed'), ('9', 'FLUID VERY HIGH'),
            ('1', '1x'), ('2', '2x'), ('4', '4x'),
            ('N/E/S/W', 'Add 5 vehicles'), ('F1-F4', 'Emergency'),
            ('SPACE', 'Pause'), ('R', 'Reset'), ('F11', 'Fullscreen'), ('Q', 'Quit')
        ]
        # --- SCREENSHOT FEATURE START ---
        if ENABLE_SCREENSHOT_FEATURE:
            controls.append(('P', 'Screenshot'))
        # --- SCREENSHOT FEATURE END ---
        x=28; cy=y+48
        for key,label in controls:
            key_s=self.small.render(key,True,WHITE)
            kw=key_s.get_width()+18
            if x+kw+105 > W-28:
                x=28; cy+=42
            pygame.draw.rect(s,PANEL_2,(x,cy,kw,29),border_radius=8)
            s.blit(key_s,(x+9,cy+5))
            self.text(label,(x+kw+8,cy+6),self.tiny,MUTED)
            x += kw + 92

        

        # --- SCREENSHOT FEATURE START ---
        if ENABLE_SCREENSHOT_FEATURE and self.last_screenshot_msg and time.time()-self.last_screenshot_time < 2.5:
            ok = not self.last_screenshot_msg.startswith('Screenshot failed')
            toast = self.small.render(self.last_screenshot_msg, True, WHITE)
            tw = toast.get_width()+28
            self.rounded_panel((W//2-tw//2, 4, tw, 30), (20,40,32) if ok else (48,20,22),
                                ACCENT if ok else RED, 10, 1)
            self.text(self.last_screenshot_msg, (W//2-tw//2+14, 11), self.small, WHITE)
        # --- SCREENSHOT FEATURE END ---

    def draw(self):
        self.draw_road()
        for v in self.world.vehicles: self.draw_vehicle(v)
        self.panel(); pygame.display.flip()

    def save_csv(self):
        out='evaluation_results.csv'
        with open(out,'w',newline='') as f:
            w=csv.writer(f); w.writerow(['timestamp','mode','lane','vehicle_count','queue_length','weighted_demand','signal_phase','signal_state','signal_remaining','vehicles_passed','wait_time','congestion_grade','avg_delay_s']); w.writerows(self.world.csv_rows)
        return out

    def loop(self):
        while self.running:
            dt=min(self.clock.tick(FPS)/1000.0,0.05)
            for e in pygame.event.get():
                if e.type==pygame.QUIT: self.running=False
                elif e.type==pygame.KEYDOWN:
                    if e.key==pygame.K_q: self.running=False
                    elif e.key==pygame.K_a: self.world.controller.set_mode('adaptive')
                    elif e.key==pygame.K_f: self.world.controller.set_mode('fixed')
                    elif e.key==pygame.K_n: self.world.add_five('N')
                    elif e.key==pygame.K_e: self.world.add_five('E')
                    elif e.key==pygame.K_s: self.world.add_five('S')
                    elif e.key==pygame.K_w: self.world.add_five('W')
                    elif e.key==pygame.K_6: self.world.set_auto(True, 'low')
                    elif e.key==pygame.K_7: self.world.set_auto(True, 'medium')
                    elif e.key==pygame.K_8: self.world.set_auto(True, 'high')
                    elif e.key==pygame.K_9: self.world.set_fluid(True)
                    elif e.key==pygame.K_0: self.world.set_auto(False)
                    elif e.key==pygame.K_1: self.world.set_sim_speed(1)
                    elif e.key==pygame.K_2: self.world.set_sim_speed(2)
                    elif e.key==pygame.K_4: self.world.set_sim_speed(4)
                    elif e.key==pygame.K_F1: self.world.add_emergency('N')
                    elif e.key==pygame.K_F2: self.world.add_emergency('E')
                    elif e.key==pygame.K_F3: self.world.add_emergency('S')
                    elif e.key==pygame.K_F4: self.world.add_emergency('W')
                    elif e.key==pygame.K_ESCAPE:
                        if self.world.controller.emergency_active: self.world.controller.finish_emergency()
                        else: self.running=False
                    elif e.key==pygame.K_SPACE: self.world.paused=not self.world.paused
                    elif e.key==pygame.K_r: self.world=World(seed=42)
                    elif e.key==pygame.K_F11: self.toggle_fullscreen()
                    # --- SCREENSHOT FEATURE START ---
                    elif ENABLE_SCREENSHOT_FEATURE and e.key==SCREENSHOT_KEY: self.take_screenshot()
                    # --- SCREENSHOT FEATURE END ---
            if not self.world.paused: self.world.update(dt * self.world.sim_speed)
            self.draw()
        self.save_csv(); pygame.quit()

if __name__=='__main__': App().loop()