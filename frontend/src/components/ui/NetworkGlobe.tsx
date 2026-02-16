import { useRef, useMemo } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { Points, PointMaterial } from "@react-three/drei";
import * as THREE from "three";

function Globe({ isDark }: { isDark: boolean }) {
    const pointsRef = useRef<THREE.Points>(null!);
    const ringRef = useRef<THREE.Mesh>(null!);

    // Generate points uniformly distributed on a sphere
    // Represents abstract data nodes connected by the system
    const particles = useMemo(() => {
        const temp = [];
        const count = 3000;

        for (let i = 0; i < count; i++) {
            const theta = Math.random() * 2 * Math.PI;
            const phi = Math.acos(2 * Math.random() - 1);
            const r = 1.2;

            const x = r * Math.sin(phi) * Math.cos(theta);
            const y = r * Math.sin(phi) * Math.sin(theta);
            const z = r * Math.cos(phi);

            temp.push(x, y, z);
        }
        return new Float32Array(temp);
    }, []);

    useFrame((state: { clock: { elapsedTime: number } }, delta: number) => {
        // Rotate globe slowly
        if (pointsRef.current) {
            pointsRef.current.rotation.y += delta * 0.05;
        }
        // Rotate ring slightly faster/differently
        if (ringRef.current) {
            ringRef.current.rotation.x = Math.PI / 2 + Math.sin(state.clock.elapsedTime * 0.2) * 0.1;
            ringRef.current.rotation.y += delta * 0.02;
        }
    });

    // Dark: Electric blue.  Light: Deep violet (high contrast on white bg)
    const primaryColor = isDark ? "#3b82f6" : "#7c3aed";
    const ringColor = isDark ? "#2563eb" : "#6d28d9";
    const particleSize = isDark ? 0.015 : 0.025;
    const ringOpacity = isDark ? 0.3 : 0.5;
    const glowOpacity = isDark ? 0.05 : 0.12;

    return (
        <group rotation={[0, 0, Math.PI / 6]}>
            {/* The Constellation Sphere */}
            <Points ref={pointsRef} positions={particles} stride={3} frustumCulled={false}>
                <PointMaterial
                    transparent
                    color={primaryColor}
                    size={particleSize}
                    sizeAttenuation={true}
                    depthWrite={false}
                    opacity={0.8}
                />
            </Points>

            {/* The Orbiting Ring */}
            <mesh ref={ringRef} rotation={[Math.PI / 2, 0, 0]}>
                <torusGeometry args={[1.6, 0.005, 16, 100]} />
                <meshBasicMaterial color={ringColor} transparent opacity={ringOpacity} />
            </mesh>

            {/* Inner Glow Sphere (Atmosphere) */}
            <mesh>
                <sphereGeometry args={[1.15, 32, 32]} />
                <meshBasicMaterial color={primaryColor} transparent opacity={glowOpacity} />
            </mesh>
        </group>
    );
}

export default function NetworkGlobe({ isDark = false }: { isDark?: boolean }) {
    return (
        <div className="w-full h-full absolute inset-0">
            <Canvas camera={{ position: [0, 0, 3.5], fov: 45 }}>
                <ambientLight intensity={0.5} />
                <Globe isDark={isDark} />
            </Canvas>
        </div>
    );
}
