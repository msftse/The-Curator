"use client";

import { useEffect, useRef } from "react";

interface Node {
  x: number;
  y: number;
  vx: number;
  vy: number;
  isAccent: boolean;
  accentColor: string;
  cluster: "left" | "right";
  igniteTime: number;
  igniteProgress: number;
}

interface Packet {
  fromNode: Node;
  toNode: Node;
  progress: number;
  startTime: number;
}

export function SynapseCanvas() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rafRef = useRef<number>();
  const nodesRef = useRef<Node[]>([]);
  const packetsRef = useRef<Packet[]>([]);
  const lastPacketTime = useRef(0);
  const lastIgniteTime = useRef(0);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext("2d", { alpha: true });
    if (!ctx) return;

    let mouseX = 0;
    let mouseY = 0;
    let isPaused = false;

    // Check for reduced motion preference
    const prefersReducedMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)"
    ).matches;

    // Setup canvas sizing with device pixel ratio
    const resizeCanvas = () => {
      const dpr = window.devicePixelRatio || 1;
      const rect = canvas.getBoundingClientRect();
      canvas.width = rect.width * dpr;
      canvas.height = rect.height * dpr;
      ctx.scale(dpr, dpr);
      canvas.style.width = `${rect.width}px`;
      canvas.style.height = `${rect.height}px`;
    };

    // Initialize nodes
    const initNodes = () => {
      const nodes: Node[] = [];
      const nodeCount = 55;
      const rect = canvas.getBoundingClientRect();

      for (let i = 0; i < nodeCount; i++) {
        // Bias towards two clusters: left (submissions) and right (catalog)
        const isLeftCluster = Math.random() < 0.5;
        const clusterCenterX = isLeftCluster
          ? rect.width * 0.25
          : rect.width * 0.75;
        const clusterCenterY = rect.height * 0.5;

        // Scatter around cluster center with gaussian-ish distribution
        const angle = Math.random() * Math.PI * 2;
        const radius = Math.random() * Math.min(rect.width, rect.height) * 0.3;
        const x = clusterCenterX + Math.cos(angle) * radius;
        const y = clusterCenterY + Math.sin(angle) * radius;

        // 15% chance of accent node
        const isAccent = Math.random() < 0.15;
        const accentColor =
          Math.random() < 0.5
            ? "rgba(139,92,246,0.75)" // Signal Violet
            : "rgba(91,95,239,0.75)"; // Curator Indigo

        nodes.push({
          x: Math.max(0, Math.min(rect.width, x)),
          y: Math.max(0, Math.min(rect.height, y)),
          vx: (Math.random() - 0.5) * 0.15,
          vy: (Math.random() - 0.5) * 0.15,
          isAccent,
          accentColor,
          cluster: isLeftCluster ? "left" : "right",
          igniteTime: 0,
          igniteProgress: 0,
        });
      }

      nodesRef.current = nodes;
    };

    // Distance threshold for drawing edges
    const EDGE_THRESHOLD = 150;
    const PARALLAX_MAX = 8;

    // Update node positions
    const updateNodes = (rect: DOMRect) => {
      nodesRef.current.forEach((node) => {
        if (!prefersReducedMotion) {
          node.x += node.vx;
          node.y += node.vy;

          // Wrap at edges
          if (node.x < 0) node.x = rect.width;
          if (node.x > rect.width) node.x = 0;
          if (node.y < 0) node.y = rect.height;
          if (node.y > rect.height) node.y = 0;

          // Update ignite animation
          if (node.igniteProgress > 0) {
            node.igniteProgress -= 0.015;
            if (node.igniteProgress < 0) node.igniteProgress = 0;
          }
        }
      });
    };

    // Draw edges between nearby nodes
    const drawEdges = (rect: DOMRect) => {
      ctx.strokeStyle = "rgba(253,246,232,0.08)";
      ctx.lineWidth = 0.5;

      for (let i = 0; i < nodesRef.current.length; i++) {
        const nodeA = nodesRef.current[i];
        for (let j = i + 1; j < nodesRef.current.length; j++) {
          const nodeB = nodesRef.current[j];
          const dx = nodeB.x - nodeA.x;
          const dy = nodeB.y - nodeA.y;
          const dist = Math.sqrt(dx * dx + dy * dy);

          if (dist < EDGE_THRESHOLD) {
            const opacity = 0.18 * (1 - dist / EDGE_THRESHOLD);
            ctx.strokeStyle = `rgba(253,246,232,${opacity})`;
            ctx.beginPath();
            ctx.moveTo(nodeA.x, nodeA.y);
            ctx.lineTo(nodeB.x, nodeB.y);
            ctx.stroke();
          }
        }
      }
    };

    // Draw nodes with subtle parallax
    const drawNodes = (rect: DOMRect) => {
      nodesRef.current.forEach((node) => {
        // Apply parallax offset
        let drawX = node.x;
        let drawY = node.y;

        if (!prefersReducedMotion) {
          const centerX = rect.width / 2;
          const centerY = rect.height / 2;
          const dx = (mouseX - centerX) / rect.width;
          const dy = (mouseY - centerY) / rect.height;
          drawX += dx * PARALLAX_MAX;
          drawY += dy * PARALLAX_MAX;
        }

        // Draw ignite pulse ring if active
        if (node.igniteProgress > 0) {
          const ringRadius = 30 * (1 - node.igniteProgress);
          const ringOpacity = 0.4 * node.igniteProgress;
          ctx.strokeStyle = `rgba(245,197,66,${ringOpacity})`;
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.arc(drawX, drawY, ringRadius, 0, Math.PI * 2);
          ctx.stroke();
        }

        // Draw node
        const nodeSize = node.isAccent ? 2.5 : 1.8;
        ctx.fillStyle = node.isAccent
          ? node.accentColor
          : "rgba(253,246,232,0.55)";

        // Brighten if igniting
        if (node.igniteProgress > 0) {
          ctx.fillStyle = node.isAccent
            ? node.accentColor.replace(/[\d.]+\)$/, "1)")
            : "rgba(253,246,232,0.95)";
        }

        ctx.beginPath();
        ctx.arc(drawX, drawY, nodeSize, 0, Math.PI * 2);
        ctx.fill();
      });
    };

    // Draw packets traveling along edges
    const drawPackets = (now: number) => {
      packetsRef.current.forEach((packet) => {
        const elapsed = now - packet.startTime;
        packet.progress = Math.min(1, elapsed / 1800); // 1.8s travel time

        if (packet.progress < 1) {
          const x =
            packet.fromNode.x +
            (packet.toNode.x - packet.fromNode.x) * packet.progress;
          const y =
            packet.fromNode.y +
            (packet.toNode.y - packet.fromNode.y) * packet.progress;

          // Draw trailing fade
          const trailLength = 8;
          for (let i = 0; i < trailLength; i++) {
            const trailProgress = Math.max(
              0,
              packet.progress - i * 0.015
            );
            const trailX =
              packet.fromNode.x +
              (packet.toNode.x - packet.fromNode.x) * trailProgress;
            const trailY =
              packet.fromNode.y +
              (packet.toNode.y - packet.fromNode.y) * trailProgress;
            const trailOpacity = 0.6 * (1 - i / trailLength);

            ctx.fillStyle = `rgba(139,92,246,${trailOpacity})`;
            ctx.beginPath();
            ctx.arc(trailX, trailY, 2, 0, Math.PI * 2);
            ctx.fill();
          }

          // Draw packet head
          ctx.fillStyle = "rgba(139,92,246,0.95)";
          ctx.shadowColor = "rgba(139,92,246,0.6)";
          ctx.shadowBlur = 8;
          ctx.beginPath();
          ctx.arc(x, y, 2.5, 0, Math.PI * 2);
          ctx.fill();
          ctx.shadowBlur = 0;
        }
      });

      // Remove completed packets
      packetsRef.current = packetsRef.current.filter((p) => p.progress < 1);
    };

    // Spawn packet from left cluster to right cluster
    const spawnPacket = (now: number) => {
      const leftNodes = nodesRef.current.filter((n) => n.cluster === "left");
      const rightNodes = nodesRef.current.filter((n) => n.cluster === "right");

      if (leftNodes.length > 0 && rightNodes.length > 0) {
        const fromNode = leftNodes[Math.floor(Math.random() * leftNodes.length)];
        const toNode = rightNodes[Math.floor(Math.random() * rightNodes.length)];

        packetsRef.current.push({
          fromNode,
          toNode,
          progress: 0,
          startTime: now,
        });
      }
    };

    // Trigger ignite pulse on random node
    const triggerIgnite = () => {
      const node =
        nodesRef.current[
          Math.floor(Math.random() * nodesRef.current.length)
        ];
      node.igniteProgress = 1;
    };

    // Main animation loop
    const animate = (now: number) => {
      if (isPaused) {
        rafRef.current = requestAnimationFrame(animate);
        return;
      }

      const rect = canvas.getBoundingClientRect();
      ctx.clearRect(0, 0, rect.width, rect.height);

      updateNodes(rect);
      drawEdges(rect);
      drawNodes(rect);

      if (!prefersReducedMotion) {
        drawPackets(now);

        // Spawn packets every 1.2-2s
        if (now - lastPacketTime.current > 1200 + Math.random() * 800) {
          spawnPacket(now);
          lastPacketTime.current = now;
        }

        // Trigger ignite every 6-10s
        if (now - lastIgniteTime.current > 6000 + Math.random() * 4000) {
          triggerIgnite();
          lastIgniteTime.current = now;
        }
      }

      rafRef.current = requestAnimationFrame(animate);
    };

    // Mouse move handler for parallax
    const handleMouseMove = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      mouseX = e.clientX - rect.left;
      mouseY = e.clientY - rect.top;
    };

    // Visibility change handler
    const handleVisibilityChange = () => {
      isPaused = document.hidden;
    };

    // Initialize
    resizeCanvas();
    initNodes();
    rafRef.current = requestAnimationFrame(animate);

    // Event listeners
    window.addEventListener("resize", () => {
      resizeCanvas();
      initNodes();
    });
    canvas.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("visibilitychange", handleVisibilityChange);

    // Cleanup
    return () => {
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current);
      }
      window.removeEventListener("resize", resizeCanvas);
      canvas.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      className="absolute inset-0 z-0"
      style={{ width: "100%", height: "100%" }}
    />
  );
}
