import gmsh
import sys

def create_step_part(filename="test_part.step"):
    gmsh.initialize()
    gmsh.model.add("TestPart")
    
    # Create a Box (10x10x10)
    # x, y, z, dx, dy, dz
    box = gmsh.model.occ.addBox(0, 0, 0, 10, 10, 10)
    
    # Create a Cylinder for subtraction (hole)
    # x, y, z, dx, dy, dz, r
    cylinder = gmsh.model.occ.addCylinder(5, 5, -1, 0, 0, 12, 3)
    
    # Boolean Difference (Cut)
    # objectDimTags, toolDimTags
    # dim=3 for volumes
    gmsh.model.occ.cut([(3, box)], [(3, cylinder)])
    
    # Synchronize CAD kernel
    gmsh.model.occ.synchronize()
    
    # Export as STEP
    gmsh.write(filename)
    gmsh.finalize()
    print(f"Generated {filename}")

if __name__ == "__main__":
    create_step_part("backend/uploads/test_part.step")
