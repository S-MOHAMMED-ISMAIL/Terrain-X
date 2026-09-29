import { useEffect, useRef } from "react";
import type { BackgroundActivity, BackgroundVariant } from "@/components/PageHeaderContext";

interface Props {
  variant: BackgroundVariant;
  activity: BackgroundActivity;
}

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  r: number;
  alpha: number;
}

interface VariantConfig {
  /** 0-1 alpha multipliers per decorative layer — tuned per page so the
   * same visual system reads as "broad ambient" on the Dashboard vs.
   * "stronger topographic" on Terrain vs. "restrained" on Reports, without
   * being a different component per page. */
  grid: number;
  contour: number;
  contourLines: number;
  particles: number;
  scan: number;
  arc: number;
  pulseHue: "brand" | "amber";
}

const VARIANTS: Record<BackgroundVariant, VariantConfig> = {
  default: { grid: 0.5, contour: 0.5, contourLines: 5, particles: 0.6, scan: 0.4, arc: 0.5, pulseHue: "brand" },
  dashboard: { grid: 0.7, contour: 0.6, contourLines: 6, particles: 1, scan: 0.5, arc: 0.7, pulseHue: "brand" },
  projects: { grid: 0.6, contour: 0.4, contourLines: 4, particles: 0.7, scan: 0.3, arc: 0.4, pulseHue: "brand" },
  analysis: { grid: 0.5, contour: 0.4, contourLines: 4, particles: 0.8, scan: 1, arc: 0.4, pulseHue: "brand" },
  terrain: { grid: 0.4, contour: 1, contourLines: 8, particles: 0.5, scan: 0.3, arc: 0.5, pulseHue: "brand" },
  disaster: { grid: 0.5, contour: 0.6, contourLines: 5, particles: 0.5, scan: 0.4, arc: 0.4, pulseHue: "amber" },
  reports: { grid: 0.6, contour: 0.2, contourLines: 3, particles: 0.3, scan: 0.15, arc: 0.2, pulseHue: "brand" },
};

const TARGET_FRAME_MS = 1000 / 28; // deliberately throttled well below 60fps — slow, cheap ambient motion, not a real-time visualization.

/** A purely decorative, procedurally-animated ambient background — abstract
 * grid/contour/particle/scan/orbital motifs in the existing TERRAIN-X
 * palette, never real coordinates, elevation, hazard, or analysis values.
 * Renders on its own 2D canvas, entirely separate from TerrainView3D's
 * WebGL scene (different element, different context, mounted at the
 * AppShell layout level so it survives route changes without remounting —
 * only `variant`/`activity` change, read live via refs so the single
 * animation loop below never restarts on navigation). */
export function DynamicScientificBackground({ variant, activity }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const variantRef = useRef(variant);
  variantRef.current = variant;
  const activityRef = useRef(activity);
  activityRef.current = activity;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d", { alpha: true });
    if (!ctx) return;

    const reducedMotionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    let particles: Particle[] = [];
    let dpr = Math.min(window.devicePixelRatio || 1, 2);
    let width = 0;
    let height = 0;

    function seedParticles(count: number) {
      const rng = (seed: number) => {
        // Deterministic pseudo-random (mulberry32) — a real procedural
        // generator, not Math.random(), only so a resize re-seed doesn't
        // visibly "reshuffle" every particle's identity on every resize.
        let s = seed;
        return () => {
          s |= 0;
          s = (s + 0x6d2b79f5) | 0;
          let t = Math.imul(s ^ (s >>> 15), 1 | s);
          t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
          return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
        };
      };
      const rand = rng(1337);
      particles = Array.from({ length: count }, () => ({
        x: rand() * width,
        y: rand() * height,
        vx: (rand() - 0.5) * 6,
        vy: (rand() - 0.5) * 4 - 1.5,
        r: 0.6 + rand() * 1.6,
        alpha: 0.15 + rand() * 0.35,
      }));
    }

    function resize() {
      if (!canvas) return;
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = window.innerWidth;
      height = window.innerHeight;
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
      // Fewer particles on small/narrow screens — real responsive density
      // reduction, not just a smaller canvas showing the same count.
      const base = Math.min(70, Math.max(18, Math.round((width * height) / 26000)));
      seedParticles(base);
    }

    resize();
    const resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(document.documentElement);

    // Extremely subtle pointer-driven parallax — a few pixels at most, and
    // scroll-driven drift — read here, applied as a cheap canvas translate,
    // never enough to move real content or cause discomfort.
    let pointerX = 0;
    let pointerY = 0;
    function onPointerMove(e: PointerEvent) {
      pointerX = (e.clientX / window.innerWidth - 0.5) * 2;
      pointerY = (e.clientY / window.innerHeight - 0.5) * 2;
    }
    let scrollY = 0;
    function onScroll() {
      scrollY = window.scrollY;
    }
    window.addEventListener("pointermove", onPointerMove, { passive: true });
    window.addEventListener("scroll", onScroll, { passive: true });

    let raf = 0;
    let lastDraw = 0;
    let previousDrawTime = performance.now();
    const start = performance.now();
    let disposed = false;

    function drawGrid(t: number, cfg: VariantConfig, parallaxX: number, parallaxY: number) {
      const spacing = 64;
      const drift = (t * 0.004) % spacing;
      ctx!.strokeStyle = `rgba(148, 210, 200, ${0.035 * cfg.grid})`;
      ctx!.lineWidth = 1;
      ctx!.beginPath();
      for (let x = -spacing + drift + parallaxX; x < width + spacing; x += spacing) {
        ctx!.moveTo(x, 0);
        ctx!.lineTo(x, height);
      }
      for (let y = -spacing + (drift % spacing) + parallaxY; y < height + spacing; y += spacing) {
        ctx!.moveTo(0, y);
        ctx!.lineTo(width, y);
      }
      ctx!.stroke();
    }

    function drawContours(t: number, cfg: VariantConfig) {
      const lines = cfg.contourLines;
      for (let i = 0; i < lines; i++) {
        const baseY = height * (0.15 + (i / Math.max(1, lines - 1)) * 0.7);
        const amp = 26 + i * 4;
        const freq = 0.0016 + i * 0.00025;
        const speed = 0.00002 + i * 0.000006;
        const phase = i * 1.7;
        ctx!.beginPath();
        const steps = 48;
        for (let s = 0; s <= steps; s++) {
          const x = (s / steps) * width;
          const y = baseY + Math.sin(x * freq + t * speed + phase) * amp;
          if (s === 0) ctx!.moveTo(x, y);
          else ctx!.lineTo(x, y);
        }
        ctx!.strokeStyle = `rgba(38, 218, 192, ${0.05 * cfg.contour})`;
        ctx!.lineWidth = 1;
        ctx!.stroke();
      }
    }

    function drawParticles(dtMs: number, cfg: VariantConfig) {
      const dtSec = Math.min(dtMs / 1000, 0.1);
      ctx!.fillStyle = "rgba(148, 210, 200, 1)";
      for (const p of particles) {
        p.x += p.vx * dtSec;
        p.y += p.vy * dtSec;
        if (p.x < -10) p.x = width + 10;
        if (p.x > width + 10) p.x = -10;
        if (p.y < -10) p.y = height + 10;
        if (p.y > height + 10) p.y = -10;
        ctx!.globalAlpha = p.alpha * cfg.particles;
        ctx!.beginPath();
        ctx!.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx!.fill();
      }
      ctx!.globalAlpha = 1;
    }

    function drawScanSweep(t: number, cfg: VariantConfig, active: boolean) {
      const period = active ? 5200 : 9000; // a real analysis in progress -> a livelier, faster sweep
      const progress = (t % period) / period;
      const x = progress * (width + 400) - 200;
      const grad = ctx!.createLinearGradient(x - 140, 0, x + 140, 0);
      const peak = (active ? 0.09 : 0.05) * cfg.scan;
      grad.addColorStop(0, "rgba(38, 218, 192, 0)");
      grad.addColorStop(0.5, `rgba(38, 218, 192, ${peak})`);
      grad.addColorStop(1, "rgba(38, 218, 192, 0)");
      ctx!.fillStyle = grad;
      ctx!.fillRect(0, 0, width, height);
    }

    function drawOrbitalArc(t: number, cfg: VariantConfig) {
      const cx = width * 0.82;
      const cy = height * 0.22;
      const radius = Math.min(width, height) * 0.55;
      const rotation = t * 0.00003;
      ctx!.strokeStyle = `rgba(148, 210, 200, ${0.05 * cfg.arc})`;
      ctx!.lineWidth = 1.2;
      ctx!.beginPath();
      ctx!.arc(cx, cy, radius, rotation, rotation + Math.PI * 0.55);
      ctx!.stroke();
      // A small "satellite" marker riding the arc — decorative only.
      const markerAngle = rotation;
      const mx = cx + Math.cos(markerAngle) * radius;
      const my = cy + Math.sin(markerAngle) * radius;
      ctx!.fillStyle = `rgba(38, 218, 192, ${0.35 * cfg.arc})`;
      ctx!.beginPath();
      ctx!.arc(mx, my, 2.2, 0, Math.PI * 2);
      ctx!.fill();
    }

    function drawPulse(t: number, cfg: VariantConfig) {
      const breathe = 0.5 + 0.5 * Math.sin(t * 0.00025);
      const color = cfg.pulseHue === "amber" ? "245, 158, 11" : "38, 218, 192";
      const grad = ctx!.createRadialGradient(
        width * 0.85,
        height * 0.1,
        0,
        width * 0.85,
        height * 0.1,
        Math.max(width, height) * 0.6,
      );
      grad.addColorStop(0, `rgba(${color}, ${0.05 + 0.03 * breathe})`);
      grad.addColorStop(1, `rgba(${color}, 0)`);
      ctx!.fillStyle = grad;
      ctx!.fillRect(0, 0, width, height);
    }

    function draw(now: number) {
      if (disposed) return;
      raf = requestAnimationFrame(draw);
      if (now - lastDraw < TARGET_FRAME_MS) return;
      const dt = now - previousDrawTime;
      previousDrawTime = now;
      lastDraw = now;

      const cfg = VARIANTS[variantRef.current];
      const t = now - start;
      const active = activityRef.current === "active";
      const parallaxX = pointerX * 8;
      const parallaxY = pointerY * 6 + Math.min(scrollY * 0.02, 20);

      ctx!.clearRect(0, 0, width, height);
      drawPulse(t, cfg);
      drawGrid(t, cfg, parallaxX, parallaxY);
      drawContours(t, cfg);
      drawOrbitalArc(t, cfg);
      drawScanSweep(t, cfg, active);
      drawParticles(dt, cfg);
    }

    function drawStaticFrame() {
      // Reduced-motion: one real, still composition — no animation loop at
      // all, not merely a paused one.
      const cfg = VARIANTS[variantRef.current];
      ctx!.clearRect(0, 0, width, height);
      drawPulse(0, cfg);
      drawGrid(0, cfg, 0, 0);
      drawContours(0, cfg);
      drawOrbitalArc(0, cfg);
    }

    function start_() {
      if (reducedMotionQuery.matches) {
        drawStaticFrame();
        return;
      }
      raf = requestAnimationFrame(draw);
    }

    function onVisibilityChange() {
      if (document.visibilityState === "hidden") {
        cancelAnimationFrame(raf);
        raf = 0;
      } else if (!raf && !reducedMotionQuery.matches) {
        lastDraw = 0;
        raf = requestAnimationFrame(draw);
      }
    }
    function onReducedMotionChange() {
      cancelAnimationFrame(raf);
      raf = 0;
      if (reducedMotionQuery.matches) {
        drawStaticFrame();
      } else {
        lastDraw = 0;
        raf = requestAnimationFrame(draw);
      }
    }

    document.addEventListener("visibilitychange", onVisibilityChange);
    reducedMotionQuery.addEventListener("change", onReducedMotionChange);
    start_();

    return () => {
      disposed = true;
      cancelAnimationFrame(raf);
      resizeObserver.disconnect();
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("scroll", onScroll);
      document.removeEventListener("visibilitychange", onVisibilityChange);
      reducedMotionQuery.removeEventListener("change", onReducedMotionChange);
    };
  }, []);

  return (
    <div
      aria-hidden="true"
      className="pointer-events-none fixed inset-0 -z-10 overflow-hidden bg-terrain-950"
    >
      {/* Static CSS base gradient (cheap, GPU-composited) — the animated
          canvas above draws only the moving decorative layers, never the
          base fill, keeping per-frame canvas work minimal. */}
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top_right,_rgba(13,191,168,0.08),_transparent_60%),radial-gradient(ellipse_at_bottom_left,_rgba(13,191,168,0.05),_transparent_55%)]" />
      <canvas ref={canvasRef} data-decorative-canvas="true" className="absolute inset-0" />
      {/* A very faint vignette so foreground white cards stay the clear
          visual priority against the ambient environment. */}
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,_transparent_40%,_rgba(5,7,10,0.35)_100%)]" />
    </div>
  );
}
