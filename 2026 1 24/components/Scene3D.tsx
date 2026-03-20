import React, { Suspense, useRef, useState, useEffect, useMemo } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { OrbitControls, Grid, GizmoHelper, GizmoViewcube, GizmoViewport, Text, Html } from '@react-three/drei';
import * as THREE from 'three';
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { Material, AnyBoundaryCondition, MeshSettings } from '../types';
import { Eye, EyeOff } from 'lucide-react';

  // Custom Shader for Simulation Results (Rainbow Color Map)
const SimulationResultShader = {
  vertexShader: `
    varying vec3 vPosition;
    varying vec3 vNormal;
    varying float vStress;
    attribute float stress; 
    // attribute vec3 displacement;
    
    void main() {
      vPosition = position;
      vNormal = normal;
      vStress = stress;
      
      // Apply deformation (placeholder - needs displacement attribute)
      vec3 deformedPosition = position; 
      
      gl_Position = projectionMatrix * modelViewMatrix * vec4(deformedPosition, 1.0);
    }
  `,
  fragmentShader: `
    varying vec3 vPosition;
    varying vec3 vNormal;
    varying float vStress;
    uniform float minVal;
    uniform float maxVal;
    uniform vec3 color1; // Blue
    uniform vec3 color2; // Cyan
    uniform vec3 color3; // Green
    uniform vec3 color4; // Yellow
    uniform vec3 color5; // Red
    
    // Function to map value to rainbow color
    vec3 getRainbowColor(float value, float min, float max) {
      float t = clamp((value - min) / (max - min), 0.0, 1.0);
      
      if (t < 0.25) return mix(color1, color2, t * 4.0);
      if (t < 0.5) return mix(color2, color3, (t - 0.25) * 4.0);
      if (t < 0.75) return mix(color3, color4, (t - 0.5) * 4.0);
      return mix(color4, color5, (t - 0.75) * 4.0);
    }

    void main() {
      // Use the actual stress value passed from vertex shader
      vec3 color = getRainbowColor(vStress, minVal, maxVal);
      
      // Add simple lighting
      vec3 lightDir = normalize(vec3(1.0, 1.0, 1.0));
      float diff = max(dot(normalize(vNormal), lightDir), 0.3);
      
      gl_FragColor = vec4(color * diff, 1.0);
    }
  `
};

interface ModelViewerProps {
  modelUrl?: string | null;
  selectedMaterial?: Material | null;
  boundaryConditions?: AnyBoundaryCondition[];
  meshSettings?: MeshSettings | null;
  meshData?: any;
  faces?: any[];
  onSelect?: (type: 'face' | 'edge' | 'vertex', index: number) => void;
  scale?: number;
  rotation?: THREE.Euler;
  showLabels?: boolean;
}

const ModelViewer: React.FC<ModelViewerProps> = ({ 
  modelUrl, 
  selectedMaterial, 
  boundaryConditions = [], 
  meshSettings,
  meshData,
  faces,
  onSelect,
  scale = 1,
  rotation = new THREE.Euler(0, 0, 0),
  showLabels = true
}) => {
  const meshRef = useRef<THREE.Mesh>(null);
  const [loadedGeometry, setLoadedGeometry] = useState<THREE.BufferGeometry | null>(null);
  const [realMeshGeometry, setRealMeshGeometry] = useState<THREE.BufferGeometry | null>(null);

  const [highlightedFace, setHighlightedFace] = useState<{ normal: THREE.Vector3, center: THREE.Vector3 } | null>(null);
  const [highlightedGeometry, setHighlightedGeometry] = useState<THREE.BufferGeometry | null>(null);
  
  const highlightMaterial = useMemo(() => new THREE.MeshBasicMaterial({ 
    color: '#ffff00', 
    side: THREE.DoubleSide,
    transparent: true,
    opacity: 0.3,
    depthTest: false // Always show on top
  }), []);

  // Helper to get center of an entity (face/vertex) for visualization
  const getEntityPosition = (type: 'face' | 'vertex', index: number): THREE.Vector3 => {
      // 1. Try to use B-Rep Metadata from Backend
      if (type === 'face' && faces && faces.length > 0) {
          const face = faces.find(f => f.id === index);
          if (face) return new THREE.Vector3(face.center[0], face.center[1], face.center[2]);
      }

      // 2. Fallback Heuristic using Bounding Box of Loaded Geometry
      if (loadedGeometry) {
          loadedGeometry.computeBoundingBox();
          const bbox = loadedGeometry.boundingBox;
          if (bbox) {
              const center = new THREE.Vector3();
              bbox.getCenter(center);
              const size = new THREE.Vector3();
              bbox.getSize(size);
              const min = bbox.min;
              const max = bbox.max;

              if (type === 'face') {
                  // 0: Min X, 1: Max X, 2: Min Y, 3: Max Y, 4: Min Z, 5: Max Z
                  switch(index % 6) {
                      case 0: return new THREE.Vector3(min.x, center.y, center.z);
                      case 1: return new THREE.Vector3(max.x, center.y, center.z);
                      case 2: return new THREE.Vector3(center.x, min.y, center.z);
                      case 3: return new THREE.Vector3(center.x, max.y, center.z);
                      case 4: return new THREE.Vector3(center.x, center.y, min.z);
                      case 5: return new THREE.Vector3(center.x, center.y, max.z);
                  }
              } else if (type === 'vertex') {
                   // If meshData is available, use it (Mesh nodes)
                   if (meshData && meshData.nodes && meshData.nodes.length > index) {
                       const n = meshData.nodes[index];
                       return new THREE.Vector3(n[0], n[1], n[2]);
                   }
                   // If not, use loadedGeometry (Geometry vertices)
                   if (loadedGeometry) {
                       const posAttr = loadedGeometry.getAttribute('position');
                       if (posAttr && index >= 0 && index < posAttr.count) {
                           const v = new THREE.Vector3();
                           v.fromBufferAttribute(posAttr, index);
                           return v;
                       }
                   }
                   return center; // Fallback
              }
          }
      }
      return new THREE.Vector3(0,0,0);
  };

  // Handle click to select face, edge, or vertex
  const handleClick = (e: THREE.Event) => {
    e.stopPropagation();
    // if (displayMode === 'result') return; 

    if (e.face && loadedGeometry) {
        const point = e.point; // World intersection point
        const normal = e.face.normal.clone();
        
        // --- 1. Vertex Selection Check ---
        // Find closest vertex in the geometry to the click point
        const positionAttribute = loadedGeometry.getAttribute('position');
        let minVertexDist = Infinity;
        let closestVertexIndex = -1;
        
        // We need to iterate over vertices of the clicked face (triangle)
        // e.face.a, e.face.b, e.face.c are indices
        const faceIndices = [e.face.a, e.face.b, e.face.c];
        
        for (const idx of faceIndices) {
            const v = new THREE.Vector3();
            v.fromBufferAttribute(positionAttribute, idx);
            // Apply mesh world matrix if needed? 
            // The mesh is at 0,0,0 scale 1,1,1 rot 0,0,0 usually, but let's be safe
            if (meshRef.current) {
                v.applyMatrix4(meshRef.current.matrixWorld);
            }
            
            const dist = v.distanceTo(point);
            if (dist < minVertexDist) {
                minVertexDist = dist;
                closestVertexIndex = idx;
            }
        }
        
        // Threshold for vertex selection (e.g. 0.5 units)
        // Adjust based on model scale?
        if (minVertexDist < 0.5) {
            console.log(`Selected Vertex: ${closestVertexIndex}`);
            onSelect?.('vertex', closestVertexIndex);
            
            // Highlight Vertex
            setHighlightedFace({
                normal: normal, // Keep normal for orientation
                center: point.clone() // Or exact vertex pos
            });
            // Clear face highlight
            setHighlightedGeometry(null);
            return;
        }

        // --- 2. Edge Selection Check (Simplified) ---
        // Check distance to edges of the triangle
        // ... (Omitting for MVP, focusing on Vertex/Face first)

        // --- 3. Face Selection (Existing Logic) ---
        
        // Strategy 1: B-Rep Metadata
        if (faces && faces.length > 0) {
            let bestFaceIdx = -1;
            let minDist = Infinity;
            
            faces.forEach((face, idx) => {
                const center = new THREE.Vector3(face.center[0], face.center[1], face.center[2]);
                const dist = center.distanceTo(point);
                
                let normalMatch = true;
                if (face.normal) {
                   const faceNormal = new THREE.Vector3(face.normal[0], face.normal[1], face.normal[2]);
                   if (faceNormal.dot(normal) < 0.8) normalMatch = false;
                }
                
                if (normalMatch && dist < minDist) {
                    minDist = dist;
                    bestFaceIdx = face.id;
                }
            });
            
            if (bestFaceIdx !== -1) {
                 onSelect?.('face', bestFaceIdx);
                 console.log(`Selected B-Rep Face ID: ${bestFaceIdx}`);
                 setHighlightedFace({
                    normal: normal,
                    center: point.clone()
                 });
                 // Need to generate B-Rep face geometry for highlight?
                 // For now, clear highlight or use marker
                 setHighlightedGeometry(null); 
                 return;
            }
        }

        // Strategy 2: Fallback Heuristic
        let faceIndex = -1;
        const absX = Math.abs(normal.x);
        const absY = Math.abs(normal.y);
        const absZ = Math.abs(normal.z);
        
        if (absX > absY && absX > absZ) {
            faceIndex = normal.x < 0 ? 0 : 1; 
        } else if (absY > absX && absY > absZ) {
            faceIndex = normal.y < 0 ? 2 : 3; 
        } else {
            faceIndex = normal.z < 0 ? 4 : 5; 
        }
        
        if (faceIndex !== -1) {
            onSelect?.('face', faceIndex);
            
            // Highlight Face
            const posAttr = loadedGeometry.getAttribute('position');
            const normAttr = loadedGeometry.getAttribute('normal');
            
            if (posAttr && normAttr) {
                const highlightIndices = [];
                const localNormal = new THREE.Vector3();
                
                for (let i = 0; i < posAttr.count; i += 3) {
                    localNormal.fromBufferAttribute(normAttr, i);
                    if (localNormal.dot(normal) > 0.9) {
                        highlightIndices.push(i, i+1, i+2);
                    }
                }
                
                const highlightGeo = new THREE.BufferGeometry();
                const highlightPos = new Float32Array(highlightIndices.length * 3);
                
                for (let k = 0; k < highlightIndices.length; k++) {
                    const idx = highlightIndices[k];
                    highlightPos[k*3] = posAttr.getX(idx);
                    highlightPos[k*3+1] = posAttr.getY(idx);
                    highlightPos[k*3+2] = posAttr.getZ(idx);
                }
                
                highlightGeo.setAttribute('position', new THREE.BufferAttribute(highlightPos, 3));
                // Slightly offset to prevent z-fighting
                highlightGeo.translate(normal.x * 0.01, normal.y * 0.01, normal.z * 0.01);
                setHighlightedGeometry(highlightGeo);
            }

            setHighlightedFace({
                normal: normal,
                center: point.clone()
            });
        }
    }
  };

  // Load Model Logic
  useEffect(() => {
    if (!modelUrl) {
      setLoadedGeometry(null);
      return;
    }

    console.log("Loading model from:", modelUrl);
    
    // Determine loader based on file extension
    const urlWithoutQuery = modelUrl.split('?')[0];
    const extension = urlWithoutQuery.split('.').pop()?.toLowerCase();
    
    if (extension === 'stl') {
      const loader = new STLLoader();
      
      // Ensure the URL is correctly loaded by adding a timestamp to prevent caching if needed
      // const loadUrl = modelUrl.includes('?') ? modelUrl : `${modelUrl}?t=${Date.now()}`;
      
      loader.load(
        modelUrl,
        (geometry) => {
          console.log("STL Loaded successfully!", geometry);
          if (!geometry.attributes.normal) {
             geometry.computeVertexNormals();
          }
          // Scale down if the model is too big (e.g. from mm to meters)
          geometry.computeBoundingSphere();
          if (geometry.boundingSphere && geometry.boundingSphere.radius > 100) {
            geometry.scale(0.01, 0.01, 0.01);
          }
          // Center the geometry for better viewing
          geometry.center();
          setLoadedGeometry(geometry);
        },
        (xhr) => {
          console.log('STL Loading:', (xhr.loaded / xhr.total) * 100 + '% loaded');
        },
        (err) => {
          console.error("STL Load failed", err);
        }
      );
    } else {
      // Try GLTF as fallback for other formats if converted, or handle accordingly
      const gltfLoader = new GLTFLoader();
      gltfLoader.load(
        modelUrl, 
        (gltf) => {
          console.log("GLTF Loaded!");
          let found = false;
          gltf.scene.traverse((child) => {
              if (child instanceof THREE.Mesh && !found) {
                  child.geometry.center();
                  setLoadedGeometry(child.geometry);
                  found = true;
              }
          });
        }, 
        undefined, 
        (gltfErr) => {
           console.error("GLTF load failed", gltfErr);
        }
      );
    }
  }, [modelUrl]);

  // Construct Real Mesh Geometry from Backend Data
  useEffect(() => {
    if (meshData && meshData.nodes && meshData.elements) {
      const geometry = new THREE.BufferGeometry();
      const vertices: number[] = [];
      const stressValues: number[] = [];
      
      // If we have stresses, we need to map them to vertices
      // meshData.stresses is per-node, but we are rendering flat shading (vertices duplicated per face)
      // So we need to look up stress for each vertex
      
      meshData.elements.forEach((elem: number[]) => {
        // Gmsh usually returns 1-based indices for elements, but backend converts to 0-based.
        // Let's ensure we are using 0-based indexing.
        const n1 = elem[0]; const n2 = elem[1]; const n3 = elem[2]; const n4 = elem[3];
        
        const getV = (idx: number) => {
             // Robust check for index bounds
             if (idx >= 0 && idx < meshData.nodes.length) {
                 return meshData.nodes[idx];
             }
             return null;
        };

        const v1 = getV(n1); const v2 = getV(n2); const v3 = getV(n3); const v4 = getV(n4);
        
        if (v1 && v2 && v3 && v4) {
             // Face 1: 1-2-3
             vertices.push(...v1, ...v2, ...v3);
             // Face 2: 1-3-4
             vertices.push(...v1, ...v3, ...v4);
             // Face 3: 1-4-2
             vertices.push(...v1, ...v4, ...v2);
             // Face 4: 2-4-3
             vertices.push(...v2, ...v4, ...v3);
             
             // If solved, add stress values
             if (meshData.stresses && meshData.stresses.length > 0) {
                 const getS = (idx: number) => meshData.stresses[idx] || 0;
                 const s1 = getS(n1); const s2 = getS(n2); const s3 = getS(n3); const s4 = getS(n4);
                 
                 // Face 1: 1-2-3
                 stressValues.push(s1, s2, s3);
                 // Face 2: 1-3-4
                 stressValues.push(s1, s3, s4);
                 // Face 3: 1-4-2
                 stressValues.push(s1, s4, s2);
                 // Face 4: 2-4-3
                 stressValues.push(s2, s4, s3);
             }
        }
      });
      
      if (vertices.length > 0) {
          geometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
          
          if (stressValues.length > 0) {
              geometry.setAttribute('stress', new THREE.Float32BufferAttribute(stressValues, 1));
          }
          
          geometry.computeVertexNormals();
          // geometry.center(); // REMOVED: Do not re-center mesh, trust backend coordinates
          setRealMeshGeometry(geometry);
          console.log("Real mesh geometry created with vertices:", vertices.length / 3);
      } else {
          console.warn("No valid vertices generated for mesh geometry.");
      }
    }
  }, [meshData]);

  // Materials
  const geometryMaterial = useMemo(() => new THREE.MeshStandardMaterial({ 
    color: selectedMaterial?.color || '#cccccc', 
    metalness: 0.1, 
    roughness: 0.5,
    side: THREE.DoubleSide
  }), [selectedMaterial]);

  const meshMaterial = useMemo(() => new THREE.MeshStandardMaterial({ 
    color: '#e0e0e0', 
    polygonOffset: true, 
    polygonOffsetFactor: 1, 
    side: THREE.DoubleSide
  }), []);

  const resultMaterial = useMemo(() => new THREE.ShaderMaterial({
    vertexShader: SimulationResultShader.vertexShader,
    fragmentShader: SimulationResultShader.fragmentShader,
    uniforms: {
      minVal: { value: 0.0 },
      maxVal: { value: 1.0 },
      deformationScale: { value: 1.0 }, // Deformation scale factor
      color1: { value: new THREE.Color('#0000ff') },
      color2: { value: new THREE.Color('#00ffff') },
      color3: { value: new THREE.Color('#00ff00') },
      color4: { value: new THREE.Color('#ffff00') },
      color5: { value: new THREE.Color('#ff0000') },
    },
    side: THREE.DoubleSide
  }), []);

  // Update uniforms when result data changes
  useEffect(() => {
    if (meshSettings?.status === 'solved' && meshData && meshData.stresses) {
        // Find min/max stress
        const minS = Math.min(...meshData.stresses);
        const maxS = Math.max(...meshData.stresses);
        resultMaterial.uniforms.minVal.value = minS;
        resultMaterial.uniforms.maxVal.value = maxS;
        
        // Auto-calculate deformation scale
        if (meshData.max_displacement > 0) {
            const targetVisualDisp = 0.5;
            const scaleFactor = targetVisualDisp / meshData.max_displacement;
            resultMaterial.uniforms.deformationScale.value = scaleFactor;
        }

        // We already set attributes in the geometry construction effect
        // Just need to ensure material knows it needs update
        resultMaterial.needsUpdate = true;
    }
  }, [meshSettings, meshData, resultMaterial]);

  // Determine what to render
  const geometryToRender = meshSettings?.status === 'solved' || meshSettings?.status === 'meshed' 
      ? (realMeshGeometry || loadedGeometry) 
      : loadedGeometry;
      
  const materialToRender = meshSettings?.status === 'solved' 
      ? resultMaterial 
      : (meshSettings?.status === 'meshed' ? meshMaterial : geometryMaterial);

  return (
    <group scale={scale} rotation={rotation}>
       {geometryToRender && (
         <mesh 
           ref={meshRef} 
           geometry={geometryToRender} 
           material={materialToRender} 
           castShadow 
           receiveShadow
           onClick={handleClick}
         />
       )}
       {highlightedGeometry && (
         <mesh geometry={highlightedGeometry} material={highlightMaterial} />
       )}
       
       {/* Face Labels */}
       {showLabels && faces && faces.length > 0 && faces.map((face) => (
         <Html
            key={`face-label-${face.id}`}
            position={[face.center[0], face.center[1], face.center[2]]}
            center
            occlude={[meshRef]}
            zIndexRange={[100, 0]}
            style={{ pointerEvents: 'none' }}
         >
            <div style={{
              background: 'rgba(0,0,0,0.7)',
              color: '#e2e8f0',
              padding: '2px 6px',
              borderRadius: '4px',
              fontSize: '11px',
              fontWeight: 'bold',
              fontFamily: 'sans-serif',
              whiteSpace: 'nowrap',
              border: '1px solid rgba(255,255,255,0.3)',
              boxShadow: '0 2px 4px rgba(0,0,0,0.2)',
              userSelect: 'none'
            }}>
              Face {face.id}
            </div>
         </Html>
       ))}

       {/* Always render a small reference sphere at 0,0,0 to prove scene is working */}
       <mesh position={[0,0,0]}>
          <sphereGeometry args={[0.1, 16, 16]} />
          <meshBasicMaterial color="red" />
       </mesh>
    </group>
  );
};

interface Scene3DProps {
  modelUrl?: string | null;
  selectedMaterial?: Material | null;
  boundaryConditions?: AnyBoundaryCondition[];
  onSelect?: (type: 'face' | 'edge' | 'vertex', index: number) => void;
  meshSettings?: MeshSettings | null;
  meshData?: any; 
  faces?: any[];
}

const Scene3D: React.FC<Scene3DProps> = (props) => {
  const { meshSettings, meshData } = props;
  const [showLabels, setShowLabels] = useState(true);
  
  const displayMode = useMemo(() => {
    if (meshSettings?.status === 'solved') return 'result';
    return 'standard';
  }, [meshSettings]);

  const { minStress, maxStress } = useMemo(() => {
    if (!meshData || !meshData.stresses || meshData.stresses.length === 0) return { minStress: 0, maxStress: 100 };
    const min = Math.min(...meshData.stresses);
    const max = Math.max(...meshData.stresses);
    return { minStress: min, maxStress: max };
  }, [meshData]);

  return (
    <div className="w-full h-full bg-[#f0f4f8] relative">
      {/* Simulation Setup Overlay Panel */}
      <div style={{
        position: 'absolute',
        top: '20px',
        right: '20px',
        background: 'rgba(26, 30, 44, 0.6)',
        backdropFilter: 'blur(10px)',
        WebkitBackdropFilter: 'blur(10px)',
        border: '1px solid rgba(255, 255, 255, 0.1)',
        borderRadius: '12px',
        padding: '16px',
        color: '#e2e8f0',
        zIndex: 10,
        width: '240px',
        boxShadow: '0 8px 32px rgba(0, 0, 0, 0.2)',
        display: 'flex',
        flexDirection: 'column',
        gap: '12px',
        pointerEvents: 'auto' // Change to auto to allow clicking the toggle
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid rgba(255,255,255,0.1)', paddingBottom: '8px' }}>
          <h3 style={{ margin: 0, fontSize: '14px', fontWeight: 'bold', color: '#fff' }}>
            仿真设置总览
          </h3>
          <button 
             onClick={() => setShowLabels(!showLabels)}
             title={showLabels ? "隐藏面标签" : "显示面标签"}
             style={{
               background: 'transparent',
               border: 'none',
               color: showLabels ? '#60a5fa' : '#64748b',
               cursor: 'pointer',
               padding: '4px',
               display: 'flex',
               alignItems: 'center',
               justifyContent: 'center',
               borderRadius: '4px',
               transition: 'color 0.2s'
             }}
             onMouseEnter={(e) => e.currentTarget.style.backgroundColor = 'rgba(255,255,255,0.1)'}
             onMouseLeave={(e) => e.currentTarget.style.backgroundColor = 'transparent'}
           >
             {showLabels ? <Eye size={16} /> : <EyeOff size={16} />}
           </button>
        </div>
        
        {/* Material Info */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
          <span style={{ fontSize: '11px', color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.05em' }}>当前材料</span>
          {props.selectedMaterial ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px' }}>
              <div style={{ width: '10px', height: '10px', borderRadius: '50%', backgroundColor: props.selectedMaterial.color }} />
              {props.selectedMaterial.name}
            </div>
          ) : (
            <span style={{ fontSize: '13px', fontStyle: 'italic', color: '#64748b' }}>未选择材料</span>
          )}
        </div>

        {/* Mesh Status */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
          <span style={{ fontSize: '11px', color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.05em' }}>网格状态</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px' }}>
             {props.meshSettings?.status === 'meshed' ? (
                <>
                  <div style={{ width: '8px', height: '8px', borderRadius: '50%', backgroundColor: '#22c55e' }} />
                  <span style={{ color: '#e2e8f0' }}>已划分 (节点数: {props.meshData?.nodes?.length || 0})</span>
                </>
             ) : props.meshSettings?.status === 'meshing' ? (
                <>
                  <div style={{ width: '8px', height: '8px', borderRadius: '50%', backgroundColor: '#eab308' }} />
                  <span style={{ color: '#eab308' }}>划分中...</span>
                </>
             ) : (
                <>
                  <div style={{ width: '8px', height: '8px', borderRadius: '50%', backgroundColor: '#64748b' }} />
                  <span style={{ fontStyle: 'italic', color: '#64748b' }}>未划分</span>
                </>
             )}
          </div>
        </div>

        {/* Boundary Conditions List */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
          <span style={{ fontSize: '11px', color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.05em' }}>边界条件 ({props.boundaryConditions?.length || 0})</span>
          {props.boundaryConditions && props.boundaryConditions.length > 0 ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', maxHeight: '150px', overflowY: 'auto' }}>
              {props.boundaryConditions.map((bc, idx) => (
                <div key={idx} style={{ 
                  background: 'rgba(0,0,0,0.2)', 
                  padding: '6px 8px', 
                  borderRadius: '6px',
                  borderLeft: `3px solid ${bc.type === 'fixed' ? '#ef4444' : '#eab308'}`
                }}>
                  <div style={{ fontSize: '12px', fontWeight: '500', color: '#fff' }}>{bc.name}</div>
                  <div style={{ fontSize: '11px', color: '#94a3b8', marginTop: '2px' }}>
                    {bc.type === 'fixed' ? '固定约束' : '力载荷'} 
                    {bc.type === 'force' && bc.force && (
                       <span style={{ fontFamily: 'monospace', marginLeft: '4px' }}>
                         [{typeof bc.force === 'object' ? (bc.force as any).x || 0 : 0}, {typeof bc.force === 'object' ? (bc.force as any).y || 0 : 0}, {typeof bc.force === 'object' ? (bc.force as any).z || 0 : 0}] N
                       </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <span style={{ fontSize: '13px', fontStyle: 'italic', color: '#64748b' }}>未添加约束或载荷</span>
          )}
        </div>
      </div>

      <Canvas shadows camera={{ position: [15, 15, 15], fov: 45 }}>
        <color attach="background" args={['#f0f4f8']} />
        
        <Suspense fallback={null}>
             {/* Removed Center to rule out bounding box issues */}
             <ModelViewer {...props} showLabels={showLabels} />
        </Suspense>
        
        <OrbitControls makeDefault />
        <Grid args={[20, 20]} cellSize={1} cellThickness={0.5} cellColor="#cbd5e1" sectionSize={5} />
        
        <ambientLight intensity={0.6} />
        <directionalLight position={[10, 20, 10]} intensity={1.0} castShadow />

        {/* Gizmos */}
        <GizmoHelper alignment="top-right" margin={[80, 80]} renderPriority={1}>
          <GizmoViewcube 
            faces={['右', '左', '上', '下', '前', '后']}
            hoverColor="#3b82f6"
            textColor="white"
            strokeColor="#333333"
            color="#999999"
            opacity={1}
          />
        </GizmoHelper>

        <GizmoHelper alignment="bottom-right" margin={[80, 80]} renderPriority={2}>
          <GizmoViewport 
            axisColors={['#ff3653', '#0adb50', '#2c8fdf']} 
            labelColor="black" 
            hideNegativeAxes={false}
          />
        </GizmoHelper>
      </Canvas>

      {/* Legend for Result Mode */}
      {displayMode === 'result' && (
        <>
        <div style={{
            position: 'absolute',
            left: '20px',
            bottom: '20px',
            background: 'rgba(255, 255, 255, 0.9)',
            padding: '15px',
            borderRadius: '8px',
            boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
            display: 'flex',
            flexDirection: 'column',
            gap: '8px',
            backdropFilter: 'blur(5px)',
            border: '1px solid #e2e8f0',
            zIndex: 1000,
            width: '120px'
        }}>
            <h4 style={{margin: 0, fontSize: '12px', fontWeight: '600', color: '#333'}}>Von Mises 应力</h4>
            <span style={{fontSize: '10px', color: '#666'}}>(MPa)</span>
            <div style={{display: 'flex', flexDirection: 'row', height: '180px', gap: '10px', marginTop: '5px'}}>
                <div style={{
                    width: '16px', 
                    height: '100%',
                    background: 'linear-gradient(to top, #0000ff, #00ffff, #00ff00, #ffff00, #ff0000)',
                    borderRadius: '2px',
                    border: '1px solid rgba(0,0,0,0.1)'
                }}></div>
                <div style={{display: 'flex', flexDirection: 'column', justifyContent: 'space-between', fontSize: '11px', color: '#444', fontFamily: 'monospace'}}>
                    <span>{maxStress.toExponential(2)}</span>
                    <span>{(minStress + (maxStress - minStress) * 0.8).toExponential(2)}</span>
                    <span>{(minStress + (maxStress - minStress) * 0.6).toExponential(2)}</span>
                    <span>{(minStress + (maxStress - minStress) * 0.4).toExponential(2)}</span>
                    <span>{(minStress + (maxStress - minStress) * 0.2).toExponential(2)}</span>
                    <span>{minStress.toExponential(2)}</span>
                </div>
            </div>
        </div>

        {/* Reaction Forces Display */}
        {meshData?.reaction_forces && Object.keys(meshData.reaction_forces).length > 0 && (
          <div style={{
            position: 'absolute',
            left: '160px',
            bottom: '20px',
            background: 'rgba(255, 255, 255, 0.9)',
            padding: '15px',
            borderRadius: '8px',
            boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
            backdropFilter: 'blur(5px)',
            border: '1px solid #e2e8f0',
            zIndex: 1000,
            maxWidth: '250px',
            maxHeight: '200px',
            overflowY: 'auto'
          }}>
            <h4 style={{margin: 0, marginBottom: '8px', fontSize: '12px', fontWeight: '600', color: '#333'}}>支反力汇总 (N)</h4>
            <div style={{fontSize: '11px', color: '#444', display: 'flex', flexDirection: 'column', gap: '4px'}}>
              {(() => {
                let totalFx = 0, totalFy = 0, totalFz = 0;
                Object.values(meshData.reaction_forces).forEach((force: any) => {
                  totalFx += force[0];
                  totalFy += force[1];
                  totalFz += force[2];
                });
                return (
                  <>
                    <div style={{display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid #ccc', paddingBottom: '4px', marginBottom: '4px'}}>
                      <span style={{fontWeight: 'bold'}}>总和 X:</span>
                      <span style={{fontFamily: 'monospace'}}>{totalFx.toFixed(2)}</span>
                    </div>
                    <div style={{display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid #ccc', paddingBottom: '4px', marginBottom: '4px'}}>
                      <span style={{fontWeight: 'bold'}}>总和 Y:</span>
                      <span style={{fontFamily: 'monospace'}}>{totalFy.toFixed(2)}</span>
                    </div>
                    <div style={{display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid #ccc', paddingBottom: '4px'}}>
                      <span style={{fontWeight: 'bold'}}>总和 Z:</span>
                      <span style={{fontFamily: 'monospace'}}>{totalFz.toFixed(2)}</span>
                    </div>
                  </>
                );
              })()}
            </div>
          </div>
        )}
        </>
      )}
    </div>
  );
};

export default Scene3D;
