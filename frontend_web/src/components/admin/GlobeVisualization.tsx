"use client";

import { useRef, useMemo, useState, useEffect } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { OrbitControls, Sphere, Line } from "@react-three/drei";
import * as THREE from "three";
import type { TradeRoute } from "@/lib/api/types";

function latLngToVector3(lat: number, lng: number, radius: number): THREE.Vector3 {
  const phi = (90 - lat) * (Math.PI / 180);
  const theta = (lng + 180) * (Math.PI / 180);
  return new THREE.Vector3(
    -(radius * Math.sin(phi) * Math.cos(theta)),
    radius * Math.cos(phi),
    radius * Math.sin(phi) * Math.sin(theta),
  );
}

function GlobeCore() {
  const meshRef = useRef<THREE.Mesh>(null);

  const [reducedMotion, setReducedMotion] = useState(false);

  useEffect(() => {
    setReducedMotion(window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }, []);

  useFrame((_, delta) => {
    if (meshRef.current && !reducedMotion) {
      meshRef.current.rotation.y += delta * 0.05;
    }
  });

  return (
    <group>
      {/* Globe sphere */}
      <Sphere ref={meshRef} args={[2, 64, 64]}>
        <meshStandardMaterial
          color="#0a1628"
          wireframe
          transparent
          opacity={0.3}
        />
      </Sphere>
      {/* Atmosphere glow */}
      <Sphere args={[2.05, 64, 64]}>
        <meshStandardMaterial
          color="#1a3a5c"
          transparent
          opacity={0.08}
          side={THREE.BackSide}
        />
      </Sphere>
    </group>
  );
}

function TradeArc({ route }: { route: TradeRoute }) {
  const points = useMemo(() => {
    const start = latLngToVector3(route.origin.lat, route.origin.lng, 2);
    const end = latLngToVector3(route.destination.lat, route.destination.lng, 2);

    // Create arc via midpoint elevated above globe surface
    const mid = new THREE.Vector3()
      .addVectors(start, end)
      .multiplyScalar(0.5)
      .normalize()
      .multiplyScalar(2 + start.distanceTo(end) * 0.3);

    const curve = new THREE.QuadraticBezierCurve3(start, mid, end);
    return curve.getPoints(50);
  }, [route]);

  const color = route.status === "success" ? "#00ff88" : "#ff0044";

  return (
    <group>
      <Line
        points={points}
        color={color}
        lineWidth={1.5}
        transparent
        opacity={0.7}
      />
      {/* Origin point */}
      <mesh position={latLngToVector3(route.origin.lat, route.origin.lng, 2.02)}>
        <sphereGeometry args={[0.03, 8, 8]} />
        <meshBasicMaterial color="#00aaff" />
      </mesh>
      {/* Destination point */}
      <mesh position={latLngToVector3(route.destination.lat, route.destination.lng, 2.02)}>
        <sphereGeometry args={[0.03, 8, 8]} />
        <meshBasicMaterial color={color} />
      </mesh>
    </group>
  );
}

interface GlobeVisualizationProps {
  routes: TradeRoute[];
}

export function GlobeVisualization({ routes }: GlobeVisualizationProps) {
  return (
    <div className="w-full h-[400px] relative">
      <Canvas
        camera={{ position: [0, 0, 5.5], fov: 45 }}
        gl={{ antialias: true, alpha: true }}
      >
        <ambientLight intensity={0.4} />
        <pointLight position={[10, 10, 10]} intensity={0.8} />
        <pointLight position={[-10, -10, -10]} intensity={0.3} color="#1a3a5c" />

        <GlobeCore />

        {routes.map((route) => (
          <TradeArc key={route.id} route={route} />
        ))}

        <OrbitControls
          enableZoom={false}
          enablePan={false}
          autoRotate
          autoRotateSpeed={0.3}
          minPolarAngle={Math.PI * 0.3}
          maxPolarAngle={Math.PI * 0.7}
          enableDamping
        />
      </Canvas>

      {/* Label overlay */}
      <div className="absolute bottom-4 left-1/2 -translate-x-1/2 text-center">
        <p className="text-[10px] font-mono text-gray-500 tracking-widest">
          LIVE TRADE ROUTES
        </p>
        <p className="text-xs font-mono text-gray-400">
          {routes.filter((r) => r.status === "success").length} active ·{" "}
          {routes.filter((r) => r.status === "blocked").length} blocked
        </p>
      </div>
    </div>
  );
}
