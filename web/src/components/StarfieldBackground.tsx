"use client";

import { useEffect, useRef } from "react";

export default function StarfieldBackground() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let width: number, height: number;
    let stars: Star[] = [];
    let meteors: Meteor[] = [];
    const maxStars = 240;
    const connectionDistance = 120;
    const mouseDistance = 160;
    const mouse: { x: number | null; y: number | null } = { x: null, y: null };
    let animationId: number;
    let isVisible = true;

    function resize() {
      if (!canvas) return;
      width = window.innerWidth;
      height = window.innerHeight;
      canvas.width = width * window.devicePixelRatio;
      canvas.height = height * window.devicePixelRatio;
      canvas.style.width = width + "px";
      canvas.style.height = height + "px";
      ctx!.scale(window.devicePixelRatio, window.devicePixelRatio);
    }

    class Star {
      x!: number;
      y!: number;
      size!: number;
      speedX!: number;
      speedY!: number;
      opacity!: number;
      twinkleSpeed!: number;
      twinklePhase!: number;
      color!: string;

      constructor() {
        this.reset();
        this.y = Math.random() * height;
      }

      reset() {
        this.x = Math.random() * width;
        this.y = Math.random() * height;
        this.size = Math.random() * 1.4 + 0.3;
        this.speedX = (Math.random() - 0.5) * 0.12;
        this.speedY = (Math.random() - 0.5) * 0.12;
        this.opacity = Math.random() * 0.6 + 0.2;
        this.twinkleSpeed = Math.random() * 0.02 + 0.005;
        this.twinklePhase = Math.random() * Math.PI * 2;
        const rand = Math.random();
        this.color =
          rand > 0.9
            ? "155, 142, 196"
            : rand > 0.8
            ? "217, 56, 46"
            : "232, 228, 221";
      }

      update() {
        this.x += this.speedX;
        this.y += this.speedY;
        this.twinklePhase += this.twinkleSpeed;
        if (this.x < 0 || this.x > width) this.speedX *= -1;
        if (this.y < 0 || this.y > height) this.speedY *= -1;
      }

      draw() {
        const twinkle = Math.sin(this.twinklePhase) * 0.3 + 0.7;
        const alpha = this.opacity * twinkle;
        ctx!.beginPath();
        ctx!.arc(this.x, this.y, this.size, 0, Math.PI * 2);
        ctx!.fillStyle = `rgba(${this.color}, ${alpha})`;
        ctx!.fill();
        if (this.size > 1 && alpha > 0.5) {
          ctx!.beginPath();
          ctx!.arc(this.x, this.y, this.size * 3, 0, Math.PI * 2);
          ctx!.fillStyle = `rgba(${this.color}, ${alpha * 0.15})`;
          ctx!.fill();
        }
      }
    }

    class Meteor {
      x!: number;
      y!: number;
      len!: number;
      speed!: number;
      angle!: number;
      life!: number;
      maxLife!: number;
      active!: boolean;

      constructor() {
        this.reset();
      }

      reset() {
        this.x = Math.random() * width;
        this.y = Math.random() * height * 0.5;
        this.len = Math.random() * 80 + 40;
        this.speed = Math.random() * 8 + 6;
        this.angle = Math.PI / 4;
        this.life = 0;
        this.maxLife = Math.random() * 60 + 40;
        this.active = false;
      }

      spawn() {
        this.reset();
        this.active = true;
      }

      update() {
        if (!this.active) return;
        this.x += Math.cos(this.angle) * this.speed;
        this.y += Math.sin(this.angle) * this.speed;
        this.life++;
        if (
          this.life > this.maxLife ||
          this.x > width + this.len ||
          this.y > height + this.len
        ) {
          this.active = false;
        }
      }

      draw() {
        if (!this.active) return;
        const tailX = this.x - Math.cos(this.angle) * this.len;
        const tailY = this.y - Math.sin(this.angle) * this.len;
        const gradient = ctx!.createLinearGradient(tailX, tailY, this.x, this.y);
        gradient.addColorStop(0, "rgba(217, 56, 46, 0)");
        gradient.addColorStop(1, "rgba(217, 56, 46, 0.6)");
        ctx!.beginPath();
        ctx!.moveTo(tailX, tailY);
        ctx!.lineTo(this.x, this.y);
        ctx!.strokeStyle = gradient;
        ctx!.lineWidth = 1.2;
        ctx!.stroke();
      }
    }

    function init() {
      stars = [];
      meteors = [];
      for (let i = 0; i < maxStars; i++) stars.push(new Star());
      for (let i = 0; i < 3; i++) meteors.push(new Meteor());
    }

    function drawConnections() {
      for (let i = 0; i < stars.length; i++) {
        let connections = 0;
        for (let j = i + 1; j < stars.length; j++) {
          const dx = stars[i].x - stars[j].x;
          const dy = stars[i].y - stars[j].y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          if (dist < connectionDistance && connections < 2) {
            const alpha = (1 - dist / connectionDistance) * 0.08;
            ctx!.beginPath();
            ctx!.moveTo(stars[i].x, stars[i].y);
            ctx!.lineTo(stars[j].x, stars[j].y);
            ctx!.strokeStyle = `rgba(232, 228, 221, ${alpha})`;
            ctx!.lineWidth = 0.4;
            ctx!.stroke();
            connections++;
          }
        }
        if (mouse.x !== null && mouse.y !== null) {
          const dx = stars[i].x - mouse.x;
          const dy = stars[i].y - mouse.y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          if (dist < mouseDistance) {
            const alpha = (1 - dist / mouseDistance) * 0.15;
            ctx!.beginPath();
            ctx!.moveTo(stars[i].x, stars[i].y);
            ctx!.lineTo(mouse.x, mouse.y);
            ctx!.strokeStyle = `rgba(217, 56, 46, ${alpha})`;
            ctx!.lineWidth = 0.5;
            ctx!.stroke();
          }
        }
      }
    }

    function animate() {
      if (!isVisible) return;
      ctx!.clearRect(0, 0, width, height);
      stars.forEach((star) => {
        star.update();
        star.draw();
      });
      drawConnections();
      if (Math.random() < 0.005) {
        const inactive = meteors.find((m) => !m.active);
        if (inactive) inactive.spawn();
      }
      meteors.forEach((m) => {
        m.update();
        m.draw();
      });
      animationId = requestAnimationFrame(animate);
    }

    const handleResize = () => {
      resize();
      init();
    };
    const handleMouseMove = (e: MouseEvent) => {
      mouse.x = e.clientX;
      mouse.y = e.clientY;
    };
    const handleMouseLeave = () => {
      mouse.x = null;
      mouse.y = null;
    };
    const handleVisibilityChange = () => {
      isVisible = document.visibilityState === "visible";
      if (isVisible) animate();
      else if (animationId) cancelAnimationFrame(animationId);
    };

    window.addEventListener("resize", handleResize);
    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseleave", handleMouseLeave);
    document.addEventListener("visibilitychange", handleVisibilityChange);

    resize();
    init();
    animate();

    return () => {
      window.removeEventListener("resize", handleResize);
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseleave", handleMouseLeave);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      if (animationId) cancelAnimationFrame(animationId);
    };
  }, []);

  return (
    <>
      <canvas ref={canvasRef} id="starfield" aria-hidden="true" />
      <div className="aura aura-1" aria-hidden="true" />
      <div className="aura aura-2" aria-hidden="true" />
      <div className="aura aura-3" aria-hidden="true" />
    </>
  );
}
