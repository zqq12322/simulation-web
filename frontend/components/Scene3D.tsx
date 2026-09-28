import React, { Suspense, useRef, useState, useEffect, useMemo } from 'react';
import { Canvas, useFrame, ThreeEvent } from '@react-three/fiber';
import { OrbitControls, Grid, GizmoHelper, GizmoViewcube, GizmoViewport, Text, Html } from '@react-three/drei';
import * as THREE from 'three';
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { Material, AnyBoundaryCondition, MeshSettings } from '../types';
import { Eye, EyeOff, MousePointer2, Hexagon, Component, BoxSelect, Scissors } from 'lucide-react';
import { SimulationResultShader } from './resultShader';
import {
  ClipSpec,
  DEFAULT_CLIP_SPEC,
  classifyNodes,
  clipPosition,
  describeClip,
  modelBounds,
  planeEquation,
} from '../utils/clipPlane';
import {
  computeDeformationScale,
  displacementMagnitudes,
  flattenDisplacements,
  hasDisplacementField,
  modelSpanOf,
} from '../utils/deformation';
import {
  MODE_LEGEND_TITLE,
  MODE_LEGEND_UNIT,
  buildModeList,
  clampModeIndex,
  modeHint,
} from '../utils/modalModes';

interface ModelViewerProps {
  modelUrl?: string | null;
  selectedMaterial?: Material | null;
  boundaryConditions?: AnyBoundaryCondition[];
  meshSettings?: MeshSettings | null;
  meshData?: any;
  faces?: any[];
  edges?: any[];
  vertices?: any[];
  onSelect?: (type: 'face' | 'edge' | 'vertex', index: number) => void;
  onAddBoundaryCondition?: (type: 'fixed' | 'force') => void;
  scale?: number;
  rotation?: THREE.Euler;
  showLabels?: boolean;
  pickingMode?: 'face' | 'edge' | 'vertex';
  // Owned by Scene3D and shared with ModelViewer so the auto-framing effect
  // can drive the OrbitControls that are declared in Scene3D's <Canvas>.
  controlsRef?: React.RefObject<any>;
  /** 变形放大系数（无量纲），由 Scene3D 按模型尺度算出并传下来。 */
  deformationScale?: number;
  /**
   * 剖切面（世界坐标下的 three.js 平面），由 Scene3D 用纯函数算好后传下来。
   *
   * 为什么在这里才生效：材质定义在 ModelViewer 里，而 `clippingPlanes` 是
   * 材质属性。几何计算与界面控件都在 Scene3D，两边通过这一个 prop 对接，
   * 避免把同一套平面算法写两遍。
   */
  clipPlane?: THREE.Plane | null;
}

const ModelViewer: React.FC<ModelViewerProps> = ({ 
  modelUrl, 
  selectedMaterial, 
  boundaryConditions = [], 
  meshSettings,
  meshData,
  faces,
  edges,
  vertices,
  onSelect,
  onAddBoundaryCondition,
  scale = 1,
  rotation = new THREE.Euler(0, 0, 0),
  showLabels = true,
  pickingMode = 'face',
  controlsRef,
  deformationScale = 1,
  clipPlane = null,
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

  const highlightLineMaterial = useMemo(() => new THREE.LineBasicMaterial({
    color: '#ffff00',
    linewidth: 3,
    depthTest: false
  }), []);

  // Helper to get center and normal of an entity (face/edge/vertex) for visualization
  const getEntityTransform = (type: 'face' | 'edge' | 'vertex', index: number): { position: THREE.Vector3, normal: THREE.Vector3 } => {
      let position = new THREE.Vector3();
      let normal = new THREE.Vector3(0, 1, 0); // default up

      // 1. Try to use B-Rep Metadata from Backend
      if (type === 'face' && faces && faces.length > 0) {
          const face = faces.find(f => f.id === index);
          if (face) {
             position.set(face.center[0], face.center[1], face.center[2]);
             if (face.normal) normal.set(face.normal[0], face.normal[1], face.normal[2]);
             return { position, normal };
          }
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
                  switch(index % 6) {
                      case 0: position.set(min.x, center.y, center.z); normal.set(-1, 0, 0); break;
                      case 1: position.set(max.x, center.y, center.z); normal.set(1, 0, 0); break;
                      case 2: position.set(center.x, min.y, center.z); normal.set(0, -1, 0); break;
                      case 3: position.set(center.x, max.y, center.z); normal.set(0, 1, 0); break;
                      case 4: position.set(center.x, center.y, min.z); normal.set(0, 0, -1); break;
                      case 5: position.set(center.x, center.y, max.z); normal.set(0, 0, 1); break;
                  }
              } else if (type === 'vertex') {
                   if (vertices && vertices.length > 0) {
                       const v = vertices.find(v => v.id === index);
                       if (v) position.set(v.coords[0], v.coords[1], v.coords[2]);
                   } else if (meshData && meshData.nodes && meshData.nodes.length > index) {
                       const n = meshData.nodes[index];
                       position.set(n[0], n[1], n[2]);
                   } else if (loadedGeometry) {
                       const posAttr = loadedGeometry.getAttribute('position');
                       const normAttr = loadedGeometry.getAttribute('normal');
                       if (posAttr && index >= 0 && index < posAttr.count) {
                           position.fromBufferAttribute(posAttr, index);
                           if (normAttr) normal.fromBufferAttribute(normAttr, index);
                       }
                   } else {
                       position.copy(center);
                   }
              } else if (type === 'edge') {
                  if (edges && edges.length > 0) {
                      const e = edges.find(e => e.id === index);
                      if (e) position.set(e.center[0], e.center[1], e.center[2]);
                  } else {
                      // Simplified edge fallback
                      position.copy(center);
                  }
              }
          }
      }
      return { position, normal };
  };

  // Handle click to select face, edge, or vertex
  const handleClick = (e: ThreeEvent<MouseEvent>) => {
    e.stopPropagation();

    if (e.face && loadedGeometry) {
        const point = e.point; // World intersection point
        const normal = e.face.normal.clone();
        const positionAttribute = loadedGeometry.getAttribute('position');
        const faceIndices = [e.face.a, e.face.b, e.face.c];
        
        // --- 1. Vertex Selection ---
        if (pickingMode === 'vertex') {
            // Strategy 1: B-Rep Metadata
            if (vertices && vertices.length > 0) {
                let bestVertexIdx = -1;
                let minDist = Infinity;
                
                vertices.forEach(v => {
                    const vPos = new THREE.Vector3(v.coords[0], v.coords[1], v.coords[2]);
                    const dist = vPos.distanceTo(point);
                    if (dist < minDist) {
                        minDist = dist;
                        bestVertexIdx = v.id;
                    }
                });
                
                if (bestVertexIdx !== -1) {
                    onSelect?.('vertex', bestVertexIdx);
                    console.log(`Selected B-Rep Vertex ID: ${bestVertexIdx}`);
                    
                    const v = vertices.find(v => v.id === bestVertexIdx)!;
                    const vertexPos = new THREE.Vector3(v.coords[0], v.coords[1], v.coords[2]);
                    
                    const highlightGeo = new THREE.BufferGeometry();
                    highlightGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array([vertexPos.x, vertexPos.y, vertexPos.z]), 3));
                    setHighlightedGeometry(highlightGeo);
                    
                    setHighlightedFace({
                        normal: normal,
                        center: vertexPos.clone()
                    });
                    return;
                }
            }

            // Strategy 2: Mesh fallback
            let minVertexDist = Infinity;
            let closestVertexIndex = -1;
            
            for (const idx of faceIndices) {
                const v = new THREE.Vector3();
                v.fromBufferAttribute(positionAttribute, idx);
                if (meshRef.current) {
                    v.applyMatrix4(meshRef.current.matrixWorld);
                }
                
                const dist = v.distanceTo(point);
                if (dist < minVertexDist) {
                    minVertexDist = dist;
                    closestVertexIndex = idx;
                }
            }
            
            console.log(`Selected Vertex: ${closestVertexIndex}`);
            onSelect?.('vertex', closestVertexIndex);
            
            // Highlight Vertex
            const vertexPos = new THREE.Vector3();
            vertexPos.fromBufferAttribute(positionAttribute, closestVertexIndex);
            
            const highlightGeo = new THREE.BufferGeometry();
            highlightGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array([vertexPos.x, vertexPos.y, vertexPos.z]), 3));
            setHighlightedGeometry(highlightGeo);
            
            setHighlightedFace({
                normal: normal,
                center: vertexPos.clone()
            });
            return;
        }

        // --- 2. Edge Selection ---
         if (pickingMode === 'edge') {
             // Strategy 1: B-Rep Metadata
             if (edges && edges.length > 0) {
                 let bestEdgeIdx = -1;
                 let minDist = Infinity;
                 
                 edges.forEach(e => {
                     const ePos = new THREE.Vector3(e.center[0], e.center[1], e.center[2]);
                     const dist = ePos.distanceTo(point);
                     if (dist < minDist) {
                         minDist = dist;
                         bestEdgeIdx = e.id;
                     }
                 });
                 
                 if (bestEdgeIdx !== -1) {
                     onSelect?.('edge', bestEdgeIdx);
                     console.log(`Selected B-Rep Edge ID: ${bestEdgeIdx}`);
                     
                     const e = edges.find(e => e.id === bestEdgeIdx)!;
                     const edgeCenter = new THREE.Vector3(e.center[0], e.center[1], e.center[2]);
                     
                     // Approximate edge highlight as a line through center along normal cross product
                     // Or just rely on visual center highlighting
                     setHighlightedGeometry(null); 
                     
                     setHighlightedFace({
                         normal: normal,
                         center: edgeCenter.clone()
                     });
                     return;
                 }
             }

             // Strategy 2: Mesh fallback
             let minEdgeDist = Infinity;
             let closestEdgeIndex = -1;
             let edgeCenter = new THREE.Vector3();
             let bestEdgePts: [THREE.Vector3, THREE.Vector3] | null = null;
             
             // Edges of the triangle: (a,b), (b,c), (c,a)
             // NOTE: must not be named `edges` - that would shadow the prop above.
             const triangleEdges = [
                 [e.face.a, e.face.b],
                 [e.face.b, e.face.c],
                 [e.face.c, e.face.a]
             ];
 
             triangleEdges.forEach((edge, i) => {
                 const v1 = new THREE.Vector3().fromBufferAttribute(positionAttribute, edge[0]);
                 const v2 = new THREE.Vector3().fromBufferAttribute(positionAttribute, edge[1]);
                 if (meshRef.current) {
                     v1.applyMatrix4(meshRef.current.matrixWorld);
                     v2.applyMatrix4(meshRef.current.matrixWorld);
                 }
                 
                 // Distance from point to line segment v1-v2
                 const line = new THREE.Line3(v1, v2);
                 const closestPoint = new THREE.Vector3();
                 line.closestPointToPoint(point, true, closestPoint);
                 
                 const dist = closestPoint.distanceTo(point);
                 if (dist < minEdgeDist) {
                     minEdgeDist = dist;
                     // Use a simple hash for edge index based on vertex indices
                     closestEdgeIndex = Math.min(edge[0], edge[1]) * 100000 + Math.max(edge[0], edge[1]);
                     edgeCenter.copy(closestPoint);
                     bestEdgePts = [v1.clone(), v2.clone()];
                 }
             });
 
             console.log(`Selected Edge: ${closestEdgeIndex}`);
             onSelect?.('edge', closestEdgeIndex);
             
             setHighlightedFace({
                 normal: normal,
                 center: edgeCenter
             });
             
             if (bestEdgePts) {
                 const highlightGeo = new THREE.BufferGeometry();
                 highlightGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array([
                     bestEdgePts[0].x, bestEdgePts[0].y, bestEdgePts[0].z,
                     bestEdgePts[1].x, bestEdgePts[1].y, bestEdgePts[1].z
                 ]), 3));
                 setHighlightedGeometry(highlightGeo);
             } else {
                 setHighlightedGeometry(null); 
             }
             return;
         }

        // --- 3. Face Selection ---
        if (pickingMode === 'face') {
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
                    highlightGeo.translate(normal.x * 0.01, normal.y * 0.01, normal.z * 0.01);
                    setHighlightedGeometry(highlightGeo);
                }

                setHighlightedFace({
                    normal: normal,
                    center: point.clone()
                });
            }
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
          // Do not scale or center the geometry here!
          // Modifying the vertices will break coordinate mapping with the backend (Gmsh)
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
                  // Do not center the geometry to preserve original coordinates
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
      
      // Use indexed geometry for smooth shading and better performance
      const flatNodes = new Float32Array(meshData.nodes.length * 3);
      for (let i = 0; i < meshData.nodes.length; i++) {
          flatNodes[i*3] = meshData.nodes[i][0];
          flatNodes[i*3+1] = meshData.nodes[i][1];
          flatNodes[i*3+2] = meshData.nodes[i][2];
      }
      geometry.setAttribute('position', new THREE.BufferAttribute(flatNodes, 3));
      
      // 结果标量场：结构分析是 Von Mises 应力，热分析是温度。
      // Workbench 会把要着色的场统一放进 meshData.scalarField，
      // 这里回退到 stresses 以兼容既有数据。
      const scalarField: number[] | undefined =
          meshData.scalarField ?? meshData.stresses;
      if (scalarField && scalarField.length > 0) {
          const flatScalars = new Float32Array(scalarField);
          geometry.setAttribute('stress', new THREE.BufferAttribute(flatScalars, 1));
      }

      // 变形场：顶点着色器按 position + displacement * scale 显示变形。
      // 属性长度必须恰好等于顶点数，缺失时补零（见 utils/deformation.ts）。
      geometry.setAttribute(
          'displacement',
          new THREE.BufferAttribute(
              flattenDisplacements(meshData.displacements, meshData.nodes.length),
              3,
          ),
      );

      // To prevent Z-fighting and rendering internal faces of tetrahedrons,
      // we extract only the boundary faces. A boundary face is referenced exactly once.
      const faceMap = new Map<string, { count: number, indices: [number, number, number] }>();
      
      meshData.elements.forEach((elem: number[]) => {
        // Gmsh returns 0-based indices in our backend mapping
        const n1 = elem[0]; const n2 = elem[1]; const n3 = elem[2]; const n4 = elem[3];
        
        const addFace = (a: number, b: number, c: number) => {
            // Create a unique key for the face regardless of winding order
            const sorted = [a, b, c].sort((x, y) => x - y);
            const key = `${sorted[0]}_${sorted[1]}_${sorted[2]}`;
            if (faceMap.has(key)) {
                faceMap.get(key)!.count += 1;
            } else {
                // Ensure proper winding order by storing original order
                faceMap.set(key, { count: 1, indices: [a, b, c] });
            }
        };

        // Add all 4 faces of the tetrahedron
        addFace(n1, n2, n3);
        addFace(n1, n3, n4);
        addFace(n1, n4, n2);
        addFace(n2, n4, n3);
      });

      const indices: number[] = [];

      // Only add boundary faces (count === 1)
      faceMap.forEach((value) => {
          if (value.count === 1) {
              indices.push(...value.indices);
          }
      });
      
      if (indices.length > 0) {
          geometry.setIndex(indices);
          // Computing vertex normals on indexed geometry averages normals across shared vertices,
          // creating a smooth surface appearance instead of faceted triangles.
          geometry.computeVertexNormals();
          setRealMeshGeometry(geometry);
          console.log("Real mesh geometry created with surface triangles:", indices.length / 3);
      } else {
          console.warn("No valid surface vertices generated for mesh geometry.");
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

  // --- 剖切面（材质侧）-------------------------------------------------------
  useEffect(() => {
    // 恒定挂**一个**平面（未启用时它被放在包围盒之外）：three.js 的剖切面
    // 数量参与着色器程序缓存键，数量一变就要重编译，滑杆每动一下都会卡。
    const planes = clipPlane ? [clipPlane] : [];
    for (const material of [geometryMaterial, meshMaterial, resultMaterial]) {
      material.clippingPlanes = planes;
    }
  }, [clipPlane, geometryMaterial, meshMaterial, resultMaterial]);

  // Update uniforms when result data changes
  useEffect(() => {
    const scalarField: number[] | undefined = meshData?.scalarField ?? meshData?.stresses;
    if (meshSettings?.status === 'solved' && scalarField && scalarField.length > 0) {
        // 用结果场的极值确定色标范围（应力或温度）
        const minS = Math.min(...scalarField);
        const maxS = Math.max(...scalarField);
        resultMaterial.uniforms.minVal.value = minS;
        resultMaterial.uniforms.maxVal.value = maxS;

        // 放大系数由 Scene3D 按模型尺度统一计算（旧代码在这里写死 0.5，
        // 对毫米级 CAD 零件等于不显示变形、对 1 单位模型又夸张到 50%）。
        resultMaterial.uniforms.deformationScale.value = deformationScale;

        // We already set attributes in the geometry construction effect
        // Just need to ensure material knows it needs update
        resultMaterial.needsUpdate = true;
    }
  }, [meshSettings, meshData, resultMaterial, deformationScale]);

  // Determine what to render
  const geometryToRender = meshSettings?.status === 'solved' || meshSettings?.status === 'meshed' 
      ? (realMeshGeometry || loadedGeometry) 
      : loadedGeometry;

  // Auto-frame camera when geometry loads (controlsRef comes from Scene3D)
  useEffect(() => {
      const controls = controlsRef?.current;
      if (geometryToRender && controls) {
          geometryToRender.computeBoundingBox();
          const bbox = geometryToRender.boundingBox;
          if (bbox) {
              const center = new THREE.Vector3();
              bbox.getCenter(center);
              const size = new THREE.Vector3();
              bbox.getSize(size);
              
              const maxDim = Math.max(size.x, size.y, size.z);
              
              // Update controls target to the center of the model
              controls.target.copy(center);
              
              // Adjust camera position (approximate distance to fit the object)
              const distance = maxDim * 2;
              controls.object.position.set(
                  center.x + distance, 
                  center.y + distance, 
                  center.z + distance
              );
              
              controls.update();
          }
      }
  }, [geometryToRender, controlsRef]);

  const materialToRender = meshSettings?.status === 'solved' 
      ? resultMaterial 
      : (meshSettings?.status === 'meshed' ? meshMaterial : geometryMaterial);

  // The lowercase <line> intrinsic element collides with SVG's `line` in React's
  // JSX types, so the edge highlight is built as an explicit THREE.Line object.
  const highlightedLine = useMemo(() => {
      if (!highlightedGeometry) return null;
      return new THREE.Line(highlightedGeometry, highlightLineMaterial);
  }, [highlightedGeometry, highlightLineMaterial]);

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
         pickingMode === 'edge' ? (
             highlightedLine && <primitive object={highlightedLine} />
         ) : pickingMode === 'vertex' ? (
             <points geometry={highlightedGeometry}>
                 <pointsMaterial color="#ffff00" size={0.5} sizeAttenuation={true} depthTest={false} />
             </points>
         ) : (
             <mesh geometry={highlightedGeometry} material={highlightMaterial} />
         )
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

       {/* Boundary Conditions Visualization */}
       {boundaryConditions.map((bc, i) => {
           const { position, normal } = getEntityTransform(bc.applicationType as any, bc.entityIndex);
           
           if (bc.type === 'force') {
               const forceVec = new THREE.Vector3(
                   (bc.force as any)?.x || 0, 
                   (bc.force as any)?.y || 0, 
                   (bc.force as any)?.z || 0
               );
               const length = forceVec.length() || 1;
               const dir = forceVec.clone().normalize();
               if (dir.lengthSq() === 0) dir.set(0, 1, 0); // fallback direction

               return (
                   <primitive 
                       key={bc.id} 
                       object={new THREE.ArrowHelper(dir, position, Math.min(length * 0.001 + 1, 5), 0xeab308, 0.4, 0.2)} 
                   />
               );
           } else if (bc.type === 'fixed') {
               // Render fixed constraints as small red cubes or markers along the normal
               const markerPos = position.clone().add(normal.clone().multiplyScalar(0.2));
               
               // Calculate rotation to align with normal
               const quaternion = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), normal);
               const euler = new THREE.Euler().setFromQuaternion(quaternion);

               return (
                  <group key={bc.id} position={markerPos} rotation={euler}>
                     <mesh position={[0, -0.1, 0]}>
                         <coneGeometry args={[0.15, 0.3, 16]} />
                         <meshStandardMaterial color="#ef4444" />
                     </mesh>
                     <mesh position={[0, 0.1, 0]}>
                         <boxGeometry args={[0.3, 0.1, 0.3]} />
                         <meshStandardMaterial color="#b91c1c" />
                     </mesh>
                  </group>
               );
           }
           return null;
       })}

       {/* Highlighted Entity Popup */}
       {highlightedFace && (
         <Html
            position={[highlightedFace.center.x, highlightedFace.center.y, highlightedFace.center.z]}
            center
            zIndexRange={[100, 0]}
         >
            <div style={{
              background: 'rgba(26, 30, 44, 0.9)',
              padding: '6px',
              borderRadius: '8px',
              border: '1px solid rgba(255,255,255,0.2)',
              boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
              display: 'flex',
              gap: '6px',
              pointerEvents: 'auto',
              transform: 'translateY(-20px)' // Move it slightly above the center
            }}>
               <button
                 onClick={(e) => { e.stopPropagation(); onAddBoundaryCondition?.('fixed'); }}
                 style={{
                    background: '#ef4444',
                    color: 'white',
                    border: 'none',
                    padding: '4px 8px',
                    borderRadius: '4px',
                    fontSize: '12px',
                    cursor: 'pointer',
                    whiteSpace: 'nowrap'
                 }}
               >
                 固定约束
               </button>
               <button
                 onClick={(e) => { e.stopPropagation(); onAddBoundaryCondition?.('force'); }}
                 style={{
                    background: '#eab308',
                    color: 'white',
                    border: 'none',
                    padding: '4px 8px',
                    borderRadius: '4px',
                    fontSize: '12px',
                    cursor: 'pointer',
                    whiteSpace: 'nowrap'
                 }}
               >
                 力载荷
               </button>
               <button
                 onClick={(e) => { e.stopPropagation(); setHighlightedFace(null); setHighlightedGeometry(null); }}
                 style={{
                    background: 'transparent',
                    color: '#94a3b8',
                    border: '1px solid #333844',
                    padding: '4px 8px',
                    borderRadius: '4px',
                    fontSize: '12px',
                    cursor: 'pointer',
                 }}
               >
                 取消
               </button>
            </div>
         </Html>
       )}

    </group>
  );
};

interface Scene3DProps {
  modelUrl?: string | null;
  selectedMaterial?: Material | null;
  boundaryConditions?: AnyBoundaryCondition[];
  onSelect?: (type: 'face' | 'edge' | 'vertex', index: number) => void;
  onAddBoundaryCondition?: (type: 'fixed' | 'force') => void;
  meshSettings?: MeshSettings | null;
  meshData?: any; 
  faces?: any[];
  edges?: any[];
  vertices?: any[];
  /** 结果类型：结构显示 Von Mises 应力(Pa)，热分析显示温度(°C)，
   *  模态显示振型的相对位移（归一化、无量纲）。默认结构。 */
  resultKind?: 'structural' | 'thermal' | 'modal';
  /** 模态分析的固有频率列表（Hz），用于阶次选择面板 */
  modeFrequencies?: number[];
  /** 后端报告的频率≈0 的刚体模态个数 */
  rigidBodyModes?: number;
  /** 当前显示的阶次（0 起） */
  selectedMode?: number;
  /** 切换阶次 */
  onSelectMode?: (index: number) => void;
  /** 剖切面状态（由工作台持有，便于与导出/记录等功能共享同一份状态） */
  clipSpec?: ClipSpec;
  /** 修改剖切状态 */
  onClipChange?: (spec: ClipSpec) => void;
}

const Scene3D: React.FC<Scene3DProps> = (props) => {
  const { meshSettings, meshData } = props;
  const [showLabels, setShowLabels] = useState(true);
  const [pickingMode, setPickingMode] = useState<'face' | 'edge' | 'vertex'>('face');
  // Declared here (not inside ModelViewer) because <OrbitControls> lives in this
  // component's <Canvas>; ModelViewer receives it to auto-frame the camera.
  const controlsRef = useRef<any>(null);

  // --- 剖切面：几何全部由 utils/clipPlane.ts 的纯函数算 ----------------------
  // 那一层有精确断言（平面方程符号、剖掉多少节点），这里只做两件事：
  // 把状态喂给纯函数、把结果交给 three.js（材质在 ModelViewer 里，通过 prop 传）。
  const clipSpec = props.clipSpec ?? DEFAULT_CLIP_SPEC;
  const clipBounds = useMemo(() => modelBounds(meshData?.nodes), [meshData?.nodes]);
  const clipPlane = useMemo(() => {
    if (!clipBounds) return null;
    const equation = planeEquation(clipSpec, clipBounds);
    return new THREE.Plane(new THREE.Vector3(...equation.normal), equation.constant);
  }, [clipBounds, clipSpec]);
  const clipCounts = useMemo(
    () => (clipPlane
      ? classifyNodes(meshData?.nodes, {
          normal: [clipPlane.normal.x, clipPlane.normal.y, clipPlane.normal.z],
          constant: clipPlane.constant,
        })
      : { kept: 0, removed: 0, keptFraction: 1 }),
    [clipPlane, meshData?.nodes],
  );
  const clipText = clipBounds && clipPlane
    ? describeClip(
        clipSpec,
        clipPosition(clipBounds, clipSpec.axis, clipSpec.fraction),
        clipCounts,
      )
    : null;
  
  const displayMode = useMemo(() => {
    if (meshSettings?.status === 'solved') return 'result';
    return 'standard';
  }, [meshSettings]);

  const { minStress, maxStress } = useMemo(() => {
    const scalarField: number[] | undefined = meshData?.scalarField ?? meshData?.stresses;
    if (!scalarField || scalarField.length === 0) return { minStress: 0, maxStress: 100 };
    const min = Math.min(...scalarField);
    const max = Math.max(...scalarField);
    return { minStress: min, maxStress: max };
  }, [meshData]);

  // 是否带有可显示的位移场（结构分析 / 模态振型有，热分析没有）
  const hasDisplacements = useMemo(
    () => hasDisplacementField(meshData?.displacements, meshData?.nodes?.length || 0),
    [meshData],
  );

  /**
   * 变形放大系数：把最大位移放大到模型尺度的固定比例。
   *
   * 位移通常比模型小好几个数量级，1:1 画出来完全看不见，所以必须放大；
   * 但放大倍数必须相对模型尺寸——见 utils/deformation.ts 的说明
   * （旧实现写死 0.5 个坐标单位，在毫米零件上等于没显示）。
   */
  const deformationScale = useMemo(() => {
    if (!hasDisplacements) return 1;
    const span = modelSpanOf(meshData?.nodes);
    const peak = meshData?.max_displacement
      ?? Math.max(0, ...displacementMagnitudes(meshData?.displacements));
    return computeDeformationScale(peak, span);
  }, [hasDisplacements, meshData]);

  // ---- 模态分析：阶次列表与当前阶次的说明 ----
  const isModal = props.resultKind === 'modal';
  const rigidBodyModes = Number(props.rigidBodyModes) || 0;

  const modeEntries = useMemo(
    () => (isModal ? buildModeList(props.modeFrequencies, rigidBodyModes) : []),
    [isModal, props.modeFrequencies, rigidBodyModes],
  );

  const activeModeIndex = clampModeIndex(props.selectedMode ?? 0, modeEntries.length);
  const activeMode = activeModeIndex >= 0 ? modeEntries[activeModeIndex] : null;
  const activeModeHint = isModal ? modeHint(activeMode, rigidBodyModes) : null;

  /** 图例标题与单位：三种分析类型的物理量不同，不能共用一套标签。 */
  const legendTitle =
    props.resultKind === 'thermal' ? '温度'
      : isModal ? MODE_LEGEND_TITLE
        : 'Von Mises 应力';
  const legendUnit =
    props.resultKind === 'thermal' ? '(°C)'
      : isModal ? MODE_LEGEND_UNIT
        : '(Pa)';

  return (
    <div className="w-full h-full bg-[#f0f4f8] relative">
      
      {/* Topology Picking Toolbar */}
      <div style={{
          position: 'absolute',
          top: '20px',
          left: '50%',
          transform: 'translateX(-50%)',
          background: 'rgba(26, 30, 44, 0.8)',
          backdropFilter: 'blur(10px)',
          border: '1px solid rgba(255, 255, 255, 0.1)',
          borderRadius: '8px',
          padding: '4px',
          display: 'flex',
          gap: '4px',
          zIndex: 10,
          boxShadow: '0 4px 12px rgba(0,0,0,0.2)',
          pointerEvents: 'auto'
      }}>
          <button 
              onClick={() => setPickingMode('vertex')}
              style={{
                  background: pickingMode === 'vertex' ? 'rgba(96, 165, 250, 0.2)' : 'transparent',
                  color: pickingMode === 'vertex' ? '#60a5fa' : '#94a3b8',
                  border: 'none',
                  padding: '6px 12px',
                  borderRadius: '4px',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  fontSize: '12px',
                  fontWeight: '500',
                  transition: 'all 0.2s'
              }}
              title="拾取点 (Vertex)"
          >
              <MousePointer2 size={14} />
              点
          </button>
          <button 
              onClick={() => setPickingMode('edge')}
              style={{
                  background: pickingMode === 'edge' ? 'rgba(96, 165, 250, 0.2)' : 'transparent',
                  color: pickingMode === 'edge' ? '#60a5fa' : '#94a3b8',
                  border: 'none',
                  padding: '6px 12px',
                  borderRadius: '4px',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  fontSize: '12px',
                  fontWeight: '500',
                  transition: 'all 0.2s'
              }}
              title="拾取边 (Edge)"
          >
              <Hexagon size={14} />
              边
          </button>
          <button 
              onClick={() => setPickingMode('face')}
              style={{
                  background: pickingMode === 'face' ? 'rgba(96, 165, 250, 0.2)' : 'transparent',
                  color: pickingMode === 'face' ? '#60a5fa' : '#94a3b8',
                  border: 'none',
                  padding: '6px 12px',
                  borderRadius: '4px',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  fontSize: '12px',
                  fontWeight: '500',
                  transition: 'all 0.2s'
              }}
              title="拾取面 (Face)"
          >
              <BoxSelect size={14} />
              面
          </button>
      </div>

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

        {/* 剖切面：把零件切开看内部。几何（平面方程、剖掉多少节点）由
            utils/clipPlane.ts 的纯函数算，那部分有精确断言；这里只放控件。 */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.05em', cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={clipSpec.enabled}
              onChange={(event) => props.onClipChange?.({ ...clipSpec, enabled: event.target.checked })}
              disabled={!clipBounds}
            />
            <Scissors size={12} />
            剖切面
          </label>
          {clipSpec.enabled && clipBounds && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
              <div style={{ display: 'flex', gap: '4px' }}>
                {(['x', 'y', 'z'] as const).map(axis => (
                  <button
                    key={axis}
                    onClick={() => props.onClipChange?.({ ...clipSpec, axis })}
                    style={{
                      flex: 1, padding: '3px 0', fontSize: '11px', borderRadius: '4px',
                      cursor: 'pointer',
                      border: '1px solid #334155',
                      background: clipSpec.axis === axis ? '#2563eb' : 'transparent',
                      color: clipSpec.axis === axis ? '#fff' : '#94a3b8',
                    }}
                  >
                    {axis.toUpperCase()}
                  </button>
                ))}
                <button
                  onClick={() => props.onClipChange?.({
                    ...clipSpec,
                    keepSide: clipSpec.keepSide === 'below' ? 'above' : 'below',
                  })}
                  title="切换保留哪一侧"
                  style={{
                    padding: '3px 6px', fontSize: '11px', borderRadius: '4px',
                    cursor: 'pointer', border: '1px solid #334155',
                    background: 'transparent', color: '#94a3b8',
                  }}
                >
                  {clipSpec.keepSide === 'below' ? '留小侧' : '留大侧'}
                </button>
              </div>
              <input
                type="range"
                min={0}
                max={1}
                step={0.01}
                value={clipSpec.fraction}
                onChange={(event) => props.onClipChange?.({
                  ...clipSpec,
                  fraction: Number(event.target.value),
                })}
                style={{ width: '100%' }}
              />
              {clipText && (
                <div style={{ fontSize: '11px', color: '#94a3b8', lineHeight: 1.4 }}>
                  {clipText}
                </div>
              )}
            </div>
          )}
          {!clipBounds && (
            <div style={{ fontSize: '11px', color: '#64748b', fontStyle: 'italic' }}>
              需要先生成网格
            </div>
          )}
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

      <Canvas
        shadows
        camera={{ position: [15, 15, 15], fov: 45 }}
        // 剖切必须在渲染器上打开；材质上的 `clippingPlanes` 才会生效
        onCreated={({ gl }) => { gl.localClippingEnabled = true; }}
      >
        <color attach="background" args={['#f0f4f8']} />
        
        <Suspense fallback={null}>
             {/* Removed Center to rule out bounding box issues */}
             <ModelViewer {...props} showLabels={showLabels} pickingMode={pickingMode} controlsRef={controlsRef} deformationScale={deformationScale} clipPlane={clipPlane} />
        </Suspense>
        
        <OrbitControls ref={controlsRef} makeDefault />
        <Grid args={[200, 200]} cellSize={1} cellThickness={0.5} cellColor="#cbd5e1" sectionSize={5} />
        
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
            <h4 style={{margin: 0, fontSize: '12px', fontWeight: '600', color: '#333'}}>
              {legendTitle}
            </h4>
            {/* 结构：后端与材料 E 同单位 ⇒ Pa；热分析：Workbench 已把 K 换成 °C；
                模态：振型按最大位移归一化 ⇒ **无量纲的相对量** */}
            <span style={{fontSize: '10px', color: '#666'}}>
              {legendUnit}
            </span>
            {/* 模态：当前显示阶次的固有频率（阶次本身在右侧的阶次面板里高亮） */}
            {isModal && activeMode && (
              <span style={{fontSize: '10px', color: '#7c3aed', fontWeight: 600}}>
                {activeMode.label}
                {activeMode.isRigidBody ? ' ·刚体' : ''}
              </span>
            )}
            {/* 变形是放大显示的，必须告知倍数，否则用户会把画面上看到的
                变形量当成真实位移量。 */}
            {hasDisplacements && (
              <span style={{fontSize: '10px', color: '#2563eb'}}>
                变形放大 ×{deformationScale >= 100
                  ? deformationScale.toExponential(1)
                  : deformationScale.toFixed(1)}
              </span>
            )}
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

        {/* Modal mode list —— 模态分析的核心交互：点频率切振型。
            放在图例右侧（热分析/模态没有支反力面板，那一带是空的）。 */}
        {isModal && modeEntries.length > 0 && (
          <div style={{
            position: 'absolute',
            left: '190px',
            bottom: '20px',
            background: 'rgba(255, 255, 255, 0.92)',
            padding: '12px',
            borderRadius: '8px',
            boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
            backdropFilter: 'blur(5px)',
            border: '1px solid #e2e8f0',
            zIndex: 1000,
            width: '210px',
            maxHeight: '300px',
            overflowY: 'auto',
          }}>
            <h4 style={{margin: 0, marginBottom: '6px', fontSize: '12px', fontWeight: '600', color: '#333'}}>
              固有频率（共 {modeEntries.length} 阶）
            </h4>
            <div style={{display: 'flex', flexDirection: 'column', gap: '2px'}}>
              {modeEntries.map((entry) => {
                const active = entry.index === activeModeIndex;
                return (
                  <button
                    key={entry.index}
                    onClick={() => props.onSelectMode?.(entry.index)}
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      gap: '8px',
                      padding: '4px 6px',
                      borderRadius: '4px',
                      border: 'none',
                      cursor: 'pointer',
                      textAlign: 'left',
                      fontSize: '11px',
                      fontFamily: 'inherit',
                      background: active ? '#ede9fe' : 'transparent',
                      color: active ? '#5b21b6' : '#444',
                      fontWeight: active ? 600 : 400,
                    }}
                  >
                    <span>第 {entry.order} 阶</span>
                    <span style={{fontFamily: 'monospace'}}>
                      {entry.label}
                      {entry.isRigidBody ? ' ·刚体' : ''}
                    </span>
                  </button>
                );
              })}
            </div>
            {activeModeHint && (
              <p style={{
                margin: '8px 0 0',
                paddingTop: '6px',
                borderTop: '1px solid #e2e8f0',
                fontSize: '10px',
                lineHeight: 1.5,
                color: '#7c3aed',
              }}>
                {activeModeHint}
              </p>
            )}
          </div>
        )}

        {/* Reaction Forces Display（仅结构分析有意义；热分析/模态没有支反力） */}
        {props.resultKind === 'structural'
          && meshData?.reaction_forces
          && Object.keys(meshData.reaction_forces).length > 0 && (
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
