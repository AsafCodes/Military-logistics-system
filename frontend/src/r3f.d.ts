import type { Object3DNode } from '@react-three/fiber';
import * as THREE from 'three';

declare module '@react-three/fiber' {
    interface ThreeElements {
        mesh: Object3DNode<THREE.Mesh, typeof THREE.Mesh>;
        points: Object3DNode<THREE.Points, typeof THREE.Points>;
        pointMaterial: Object3DNode<THREE.PointsMaterial, typeof THREE.PointsMaterial>;
        group: Object3DNode<THREE.Group, typeof THREE.Group>;
        torusGeometry: Object3DNode<THREE.TorusGeometry, typeof THREE.TorusGeometry>;
        sphereGeometry: Object3DNode<THREE.SphereGeometry, typeof THREE.SphereGeometry>;
        meshBasicMaterial: Object3DNode<THREE.MeshBasicMaterial, typeof THREE.MeshBasicMaterial>;
        ambientLight: Object3DNode<THREE.AmbientLight, typeof THREE.AmbientLight>;
    }
}
