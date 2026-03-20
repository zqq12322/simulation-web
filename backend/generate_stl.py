import gmsh
import sys

def create_stl_part(filename="test_part.stl"):
    gmsh.initialize()
    gmsh.model.add("TestPartSTL")
    
    # Create a Box (10x10x10)
    box = gmsh.model.occ.addBox(0, 0, 0, 10, 10, 10)
    
    # Create a Cylinder for subtraction (hole)
    cylinder = gmsh.model.occ.addCylinder(5, 5, -1, 0, 0, 12, 3)
    
    # Boolean Difference (Cut)
    gmsh.model.occ.cut([(3, box)], [(3, cylinder)])
    
    # Synchronize CAD kernel
    gmsh.model.occ.synchronize()
    
    # Generate 2D mesh (Surface mesh is needed for STL)
    gmsh.option.setNumber("Mesh.MeshSizeMin", 1.0)
    gmsh.option.setNumber("Mesh.MeshSizeMax", 1.0)
    
    # Set to Binary format for better compatibility/performance
    gmsh.option.setNumber("Mesh.Binary", 1)
    
    gmsh.model.mesh.generate(2)
    
    # Export as STL
    gmsh.write(filename)
    gmsh.finalize()
    print(f"Generated {filename}")

if __name__ == "__main__":
    create_stl_part("backend/uploads/test_part.stl")
