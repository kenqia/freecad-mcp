import json
import logging
import socket
import xmlrpc.client
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Any, Literal

from mcp.server.fastmcp import FastMCP, Context
from mcp.types import TextContent, ImageContent

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("FreeCADMCPserver")

# 全局设置超时，防止 XMLRPC 永久阻塞
socket.setdefaulttimeout(30.0)

_only_text_feedback = False


class FreeCADConnection:
    def __init__(self, host: str = "localhost", port: int = 9875):
        self.server = xmlrpc.client.ServerProxy(
            f"http://{host}:{port}", allow_none=True
        )

    def ping(self) -> bool:
        return self.server.ping()

    def create_document(self, name: str) -> dict[str, Any]:
        return self.server.create_document(name)

    def create_object(self, doc_name: str, obj_data: dict[str, Any]) -> dict[str, Any]:
        return self.server.create_object(doc_name, obj_data)

    def edit_object(
        self, doc_name: str, obj_name: str, obj_data: dict[str, Any]
    ) -> dict[str, Any]:
        return self.server.edit_object(doc_name, obj_name, obj_data)

    def delete_object(self, doc_name: str, obj_name: str) -> dict[str, Any]:
        return self.server.delete_object(doc_name, obj_name)

    def insert_part_from_library(self, relative_path: str) -> dict[str, Any]:
        return self.server.insert_part_from_library(relative_path)

    def execute_code(self, code: str) -> dict[str, Any]:
        return self.server.execute_code(code)

    def get_active_screenshot(self, view_name: str = "Isometric") -> str | None:
        try:
            # Check if we're in a view that supports screenshots
            result = self.server.execute_code(
                """
import FreeCAD
import FreeCADGui

if FreeCAD.Gui.ActiveDocument and FreeCAD.Gui.ActiveDocument.ActiveView:
    view_type = type(FreeCAD.Gui.ActiveDocument.ActiveView).__name__
    
    # These view types don't support screenshots
    unsupported_views = ['SpreadsheetGui::SheetView', 'DrawingGui::DrawingView', 'TechDrawGui::MDIViewPage']
    
    if view_type in unsupported_views or not hasattr(FreeCAD.Gui.ActiveDocument.ActiveView, 'saveImage'):
        print("Current view does not support screenshots")
        False
    else:
        print(f"Current view supports screenshots: {view_type}")
        True
else:
    print("No active view")
    False
"""
            )

            # If the view doesn't support screenshots, return None
            if not result.get(
                "success", False
            ) or "Current view does not support screenshots" in result.get(
                "message", ""
            ):
                logger.info(
                    "Screenshot unavailable in current view (likely Spreadsheet or TechDraw view)"
                )
                return None

            # Otherwise, try to get the screenshot
            return self.server.get_active_screenshot(view_name)
        except Exception as e:
            # Log the error but return None instead of raising an exception
            logger.error(f"Error getting screenshot: {e}")
            return None

    def get_objects(self, doc_name: str) -> list[dict[str, Any]]:
        return self.server.get_objects(doc_name)

    def get_object(self, doc_name: str, obj_name: str) -> dict[str, Any]:
        return self.server.get_object(doc_name, obj_name)

    def get_parts_list(self) -> list[str]:
        return self.server.get_parts_list()
    

    def export_step(self, doc_name: str, file_path: str) -> dict[str, Any]:
        return self.server.export_step(doc_name, file_path)

    def export_stl(self, doc_name: str, file_path: str) -> dict[str, Any]:
        return self.server.export_stl(doc_name, file_path)
    
    def save_document(self, doc_name: str, file_path: str) -> dict[str, Any]:
        return self.server.save_document(doc_name, file_path)
    
    def close_document(self, doc_name: str) -> dict[str, Any]:
        return self.server.close_document(doc_name)

    def disconnect(self):
        pass


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    try:
        logger.info("FreeCADMCP server starting up")
        try:
            _ = get_freecad_connection()
            logger.info("Successfully connected to FreeCAD on startup")
        except Exception as e:
            logger.warning(f"Could not connect to FreeCAD on startup: {str(e)}")
            logger.warning(
                "Make sure the FreeCAD addon is running before using FreeCAD resources or tools"
            )
        yield {}
    finally:
        # Clean up the global connection on shutdown
        global _freecad_connection
        if _freecad_connection:
            logger.info("Disconnecting from FreeCAD on shutdown")
            _freecad_connection.disconnect()
            _freecad_connection = None
        logger.info("FreeCADMCP server shut down")


mcp = FastMCP(
    "FreeCADMCP",
    instructions="FreeCAD integration through the Model Context Protocol",
    lifespan=server_lifespan,
)


_freecad_connection: FreeCADConnection | None = None


def get_freecad_connection():
    """Get or create a persistent FreeCAD connection"""
    global _freecad_connection
    if _freecad_connection is None:
        _freecad_connection = FreeCADConnection(host="localhost", port=9875)
        if not _freecad_connection.ping():
            logger.error("Failed to ping FreeCAD")
            _freecad_connection = None
            raise Exception(
                "Failed to connect to FreeCAD. Make sure the FreeCAD addon is running."
            )
    return _freecad_connection


# Helper function to safely add screenshot to response
def add_screenshot_if_available(response, screenshot):
    """Safely add screenshot to response only if it's available"""
    if screenshot is not None and not _only_text_feedback:
        response.append(
            ImageContent(type="image", data=screenshot, mimeType="image/png")
        )
    elif not _only_text_feedback:
        # Add an informative message that will be seen by the AI model and user
        response.append(
            TextContent(
                type="text",
                text="Note: Visual preview is unavailable in the current view type (such as TechDraw or Spreadsheet). "
                "Switch to a 3D view to see visual feedback.",
            )
        )
    return response


@mcp.tool()
def create_document(ctx: Context, name: str) -> list[TextContent]:
    """Create a new document in FreeCAD.

    Args:
        name: The name of the document to create.

    Returns:
        A message indicating the success or failure of the document creation.

    Examples:
        If you want to create a document named "MyDocument", you can use the following data.
        ```json
        {
            "name": "MyDocument"
        }
        ```
    """
    freecad = get_freecad_connection()
    try:
        res = freecad.create_document(name)
        if res["success"]:
            return [
                TextContent(
                    type="text",
                    text=f"Document '{res['document_name']}' created successfully",
                )
            ]
        else:
            return [
                TextContent(
                    type="text", text=f"Failed to create document: {res['error']}"
                )
            ]
    except Exception as e:
        logger.error(f"Failed to create document: {str(e)}")
        return [TextContent(type="text", text=f"Failed to create document: {str(e)}")]


@mcp.tool()
def create_object(
    ctx: Context,
    doc_name: str,
    obj_type: str,
    obj_name: str,
    analysis_name: str | None = None,
    obj_properties: dict[str, Any] = None,
) -> list[TextContent | ImageContent]:
    """Create a new object in FreeCAD.
    Object type is starts with "Part::" or "Draft::" or "PartDesign::" or "Fem::".

    Args:
        doc_name: The name of the document to create the object in.
        obj_type: The type of the object to create (e.g. 'Part::Box', 'Part::Cylinder', 'Draft::Circle', 'PartDesign::Body', etc.).
        obj_name: The name of the object to create.
        obj_properties: The properties of the object to create.

    Returns:
        A message indicating the success or failure of the object creation and a screenshot of the object.

    Examples:
        If you want to create a cylinder with a height of 30 and a radius of 10, you can use the following data.
        ```json
        {
            "doc_name": "MyCylinder",
            "obj_name": "Cylinder",
            "obj_type": "Part::Cylinder",
            "obj_properties": {
                "Height": 30,
                "Radius": 10,
                "Placement": {
                    "Base": {
                        "x": 10,
                        "y": 10,
                        "z": 0
                    },
                    "Rotation": {
                        "Axis": {
                            "x": 0,
                            "y": 0,
                            "z": 1
                        },
                        "Angle": 45
                    }
                },
                "ViewObject": {
                    "ShapeColor": [0.5, 0.5, 0.5, 1.0]
                }
            }
        }
        ```

        If you want to create a circle with a radius of 10, you can use the following data.
        ```json
        {
            "doc_name": "MyCircle",
            "obj_name": "Circle",
            "obj_type": "Draft::Circle",
        }
        ```

        If you want to create a FEM analysis, you can use the following data.
        ```json
        {
            "doc_name": "MyFEMAnalysis",
            "obj_name": "FemAnalysis",
            "obj_type": "Fem::AnalysisPython",
        }
        ```

        If you want to create a FEM constraint, you can use the following data.
        ```json
        {
            "doc_name": "MyFEMConstraint",
            "obj_name": "FemConstraint",
            "obj_type": "Fem::ConstraintFixed",
            "analysis_name": "MyFEMAnalysis",
            "obj_properties": {
                "References": [
                    {
                        "object_name": "MyObject",
                        "face": "Face1"
                    }
                ]
            }
        }
        ```

        If you want to create a FEM mechanical material, you can use the following data.
        ```json
        {
            "doc_name": "MyFEMAnalysis",
            "obj_name": "FemMechanicalMaterial",
            "obj_type": "Fem::MaterialCommon",
            "analysis_name": "MyFEMAnalysis",
            "obj_properties": {
                "Material": {
                    "Name": "MyMaterial",
                    "Density": "7900 kg/m^3",
                    "YoungModulus": "210 GPa",
                    "PoissonRatio": 0.3
                }
            }
        }
        ```

        If you want to create a FEM mesh, you can use the following data.
        The `Part` property is required.
        ```json
        {
            "doc_name": "MyFEMMesh",
            "obj_name": "FemMesh",
            "obj_type": "Fem::FemMeshGmsh",
            "analysis_name": "MyFEMAnalysis",
            "obj_properties": {
                "Part": "MyObject",
                "ElementSizeMax": 10,
                "ElementSizeMin": 0.1,
                "MeshAlgorithm": 2
            }
        }
        ```
    """
    freecad = get_freecad_connection()
    try:
        obj_data = {
            "Name": obj_name,
            "Type": obj_type,
            "Properties": obj_properties or {},
            "Analysis": analysis_name,
        }
        res = freecad.create_object(doc_name, obj_data)
        screenshot = freecad.get_active_screenshot()

        if res["success"]:
            response = [
                TextContent(
                    type="text",
                    text=f"Object '{res['object_name']}' created successfully",
                ),
            ]
            return add_screenshot_if_available(response, screenshot)
        else:
            response = [
                TextContent(
                    type="text", text=f"Failed to create object: {res['error']}"
                ),
            ]
            return add_screenshot_if_available(response, screenshot)
    except Exception as e:
        logger.error(f"Failed to create object: {str(e)}")
        return [TextContent(type="text", text=f"Failed to create object: {str(e)}")]


@mcp.tool()
def edit_object(
    ctx: Context, doc_name: str, obj_name: str, obj_properties: dict[str, Any]
) -> list[TextContent | ImageContent]:
    """Edit an object in FreeCAD.
    This tool is used when the `create_object` tool cannot handle the object creation.

    Args:
        doc_name: The name of the document to edit the object in.
        obj_name: The name of the object to edit.
        obj_properties: The properties of the object to edit.

    Returns:
        A message indicating the success or failure of the object editing and a screenshot of the object.
    """
    freecad = get_freecad_connection()
    try:
        res = freecad.edit_object(doc_name, obj_name, {"Properties": obj_properties})
        screenshot = freecad.get_active_screenshot()

        if res["success"]:
            response = [
                TextContent(
                    type="text",
                    text=f"Object '{res['object_name']}' edited successfully",
                ),
            ]
            return add_screenshot_if_available(response, screenshot)
        else:
            response = [
                TextContent(type="text", text=f"Failed to edit object: {res['error']}"),
            ]
            return add_screenshot_if_available(response, screenshot)
    except Exception as e:
        logger.error(f"Failed to edit object: {str(e)}")
        return [TextContent(type="text", text=f"Failed to edit object: {str(e)}")]


@mcp.tool()
def delete_object(
    ctx: Context, doc_name: str, obj_name: str
) -> list[TextContent | ImageContent]:
    """Delete an object in FreeCAD.

    Args:
        doc_name: The name of the document to delete the object from.
        obj_name: The name of the object to delete.

    Returns:
        A message indicating the success or failure of the object deletion and a screenshot of the object.
    """
    freecad = get_freecad_connection()
    try:
        res = freecad.delete_object(doc_name, obj_name)
        screenshot = freecad.get_active_screenshot()

        if res["success"]:
            response = [
                TextContent(
                    type="text",
                    text=f"Object '{res['object_name']}' deleted successfully",
                ),
            ]
            return add_screenshot_if_available(response, screenshot)
        else:
            response = [
                TextContent(
                    type="text", text=f"Failed to delete object: {res['error']}"
                ),
            ]
            return add_screenshot_if_available(response, screenshot)
    except Exception as e:
        logger.error(f"Failed to delete object: {str(e)}")
        return [TextContent(type="text", text=f"Failed to delete object: {str(e)}")]


@mcp.tool()
def execute_code(ctx: Context, code: str) -> list[TextContent | ImageContent]:
    """Execute arbitrary Python code in FreeCAD.

    Args:
        code: The Python code to execute.

    Returns:
        A message indicating the success or failure of the code execution, the output of the code execution, and a screenshot of the object.
    """
    freecad = get_freecad_connection()
    try:
        res = freecad.execute_code(code)
        screenshot = freecad.get_active_screenshot()

        if res["success"]:
            response = [
                TextContent(
                    type="text", text=f"Code executed successfully: {res['message']}"
                ),
            ]
            return add_screenshot_if_available(response, screenshot)
        else:
            response = [
                TextContent(
                    type="text", text=f"Failed to execute code: {res['error']}"
                ),
            ]
            return add_screenshot_if_available(response, screenshot)
    except Exception as e:
        logger.error(f"Failed to execute code: {str(e)}")
        return [TextContent(type="text", text=f"Failed to execute code: {str(e)}")]


@mcp.tool()
def get_view(
    ctx: Context,
    view_name: Literal[
        "Isometric",
        "Front",
        "Top",
        "Right",
        "Back",
        "Left",
        "Bottom",
        "Dimetric",
        "Trimetric",
    ],
) -> list[ImageContent | TextContent]:
    """Get a screenshot of the active view.

    Args:
        view_name: The name of the view to get the screenshot of.
        The following views are available:
        - "Isometric"
        - "Front"
        - "Top"
        - "Right"
        - "Back"
        - "Left"
        - "Bottom"
        - "Dimetric"
        - "Trimetric"

    Returns:
        A screenshot of the active view.
    """
    freecad = get_freecad_connection()

    # ---  新增代码：强制相机对焦 ---
    try:
        # 发送一段代码让 FreeCAD 调整相机视角（FitAll）
        # 这相当于点击了工具栏里的 "Fit All" 按钮
        freecad.execute_code("FreeCAD.Gui.SendMsgToActiveView('ViewFit')")
    except Exception as e:
        logger.warning(f"Auto-fit camera failed: {e}")
    # -------------------------------

    screenshot = freecad.get_active_screenshot(view_name)

    if screenshot is not None:
        return [ImageContent(type="image", data=screenshot, mimeType="image/png")]
    else:
        return [
            TextContent(
                type="text",
                text="Cannot get screenshot in the current view type (such as TechDraw or Spreadsheet)",
            )
        ]


@mcp.tool()
def insert_part_from_library(
    ctx: Context, relative_path: str
) -> list[TextContent | ImageContent]:
    """Insert a part from the parts library addon.

    Args:
        relative_path: The relative path of the part to insert.

    Returns:
        A message indicating the success or failure of the part insertion and a screenshot of the object.
    """
    freecad = get_freecad_connection()
    try:
        res = freecad.insert_part_from_library(relative_path)
        screenshot = freecad.get_active_screenshot()

        if res["success"]:
            response = [
                TextContent(
                    type="text", text=f"Part inserted from library: {res['message']}"
                ),
            ]
            return add_screenshot_if_available(response, screenshot)
        else:
            response = [
                TextContent(
                    type="text",
                    text=f"Failed to insert part from library: {res['error']}",
                ),
            ]
            return add_screenshot_if_available(response, screenshot)
    except Exception as e:
        logger.error(f"Failed to insert part from library: {str(e)}")
        return [
            TextContent(
                type="text", text=f"Failed to insert part from library: {str(e)}"
            )
        ]
    
@mcp.tool()
def create_primitive(
    ctx: Context, 
    doc_name: str, 
    primitive_type: Literal["Box", "Cylinder", "Sphere", "Cone", "Torus"], 
    name: str, 
    dimensions: dict[str, float],
    placement: dict[str, Any] = None
) -> list[TextContent]:
    """
    Create a basic geometric primitive in FreeCAD.
    
    Args:
        doc_name: Document name.
        primitive_type: One of Box, Cylinder, Sphere, Cone, Torus.
        name: Name for the new object.
        dimensions: Dictionary of dimensions (e.g., {'Length': 10, 'Width': 10} for Box).
        placement: Optional placement (position/rotation).
    """
    freecad = get_freecad_connection()
    
    # 映射类型到 FreeCAD 内部名称
    type_map = {
        "Box": "Part::Box",
        "Cylinder": "Part::Cylinder",
        "Sphere": "Part::Sphere",
        "Cone": "Part::Cone",
        "Torus": "Part::Torus"
    }
    
    if primitive_type not in type_map:
        return [TextContent(type="text", text=f"Error: Unknown primitive type {primitive_type}")]

    obj_data = {
        "Name": name,
        "Type": type_map[primitive_type],
        "Properties": dimensions
    }
    
    if placement:
        obj_data["Properties"]["Placement"] = placement

    try:
        res = freecad.create_object(doc_name, obj_data)
        # 修改点：不再获取截图，避免 Pydantic 校验错误
        
        if res["success"]:
            msg = f"Success: Created {primitive_type} '{res['object_name']}'."
            return [TextContent(type="text", text=msg)]
        else:
            msg = f"Failed: {res.get('error')}"
            return [TextContent(type="text", text=msg)]
        
        # 原来的 return add_screenshot_if_available... 已被删除
        
    except Exception as e:
        return [TextContent(type="text", text=f"Critical Error: {str(e)}")]


@mcp.tool()
def create_gear(ctx: Context, doc_name: str, teeth: int, module: float) -> str:
    """
    Create a gear geometry in FreeCAD.
    Returns a text summary. The visual verification will be handled by the 'get_view' tool later.
    
    Args:
        doc_name: The name of the document.
        teeth: Number of teeth.
        module: Gear module size.
    """
    freecad = get_freecad_connection()
    
    # 依然使用三角函数计算精确几何
    code = f"""
import FreeCAD
import Part
import math
from FreeCAD import Vector

doc = FreeCAD.getDocument("{doc_name}")
if not doc:
    doc = FreeCAD.newDocument("{doc_name}")

# --- 齿轮参数 ---
num_teeth = {teeth}
mod = {module}
thickness = 10 

# 计算半径
pitch_radius = mod * num_teeth / 2.0
addendum = mod
dedendum = 1.25 * mod
outer_radius = pitch_radius + addendum
root_radius = pitch_radius - dedendum

# --- 生成轮廓 ---
points = []
for i in range(num_teeth):
    angle_base = 2 * math.pi * i / num_teeth
    angle_step = (2 * math.pi / num_teeth) / 4.0
    
    # 四点法拟合渐开线
    points.append(Vector(root_radius * math.cos(angle_base), root_radius * math.sin(angle_base), 0))
    points.append(Vector(outer_radius * math.cos(angle_base + angle_step), outer_radius * math.sin(angle_base + angle_step), 0))
    points.append(Vector(outer_radius * math.cos(angle_base + 2*angle_step), outer_radius * math.sin(angle_base + 2*angle_step), 0))
    points.append(Vector(root_radius * math.cos(angle_base + 3*angle_step), root_radius * math.sin(angle_base + 3*angle_step), 0))

points.append(points[0]) # 闭合

# --- 构建实体 ---
wire = Part.makePolygon(points)
face = Part.Face(wire)
gear_solid = face.extrude(Vector(0, 0, thickness))

# 挖孔
hole = Part.makeCylinder(mod * num_teeth / 8.0, thickness)
final_shape = gear_solid.cut(hole)

# --- 更新文档 ---
old_obj = doc.getObject(f"Gear_{{num_teeth}}T")
if old_obj:
    doc.removeObject(f"Gear_{{num_teeth}}T")

gear_obj = doc.addObject("Part::Feature", f"Gear_{{num_teeth}}T")
gear_obj.Shape = final_shape

doc.recompute()
FreeCAD.Gui.SendMsgToActiveView("ViewFit") # 强制对焦
"""
    res = freecad.execute_code(code)
    
    if res.get("success"):
        # ⚠️ 关键修改：只返回字符串，不返回 ImageContent，彻底解决 Pydantic 报错
        return f"Success: Created a 3D Gear with {teeth} teeth. Object name: Gear_{teeth}T"
    else:
        return f"Error creating gear: {res.get('error')}"

@mcp.tool()
def get_objects(ctx: Context, doc_name: str) -> list[TextContent | ImageContent]:
    """Get all objects in a document.
    You can use this tool to get the objects in a document to see what you can check or edit.

    Args:
        doc_name: The name of the document to get the objects from.

    Returns:
        A list of objects in the document and a screenshot of the document.
    """
    freecad = get_freecad_connection()
    try:
        screenshot = freecad.get_active_screenshot()
        response = [
            TextContent(type="text", text=json.dumps(freecad.get_objects(doc_name))),
        ]
        return add_screenshot_if_available(response, screenshot)
    except Exception as e:
        logger.error(f"Failed to get objects: {str(e)}")
        return [TextContent(type="text", text=f"Failed to get objects: {str(e)}")]


@mcp.tool()
def get_object(ctx: Context, doc_name: str, obj_name: str) -> list[TextContent | ImageContent]:
    """Get an object from a document.
    You can use this tool to get the properties of an object to see what you can check or edit.

    Args:
        doc_name: The name of the document to get the object from.
        obj_name: The name of the object to get.

    Returns:
        The object and a screenshot of the object.
    """
    freecad = get_freecad_connection()
    try:
        screenshot = freecad.get_active_screenshot()
        response = [
            TextContent(
                type="text", text=json.dumps(freecad.get_object(doc_name, obj_name))
            ),
        ]
        return add_screenshot_if_available(response, screenshot)
    except Exception as e:
        logger.error(f"Failed to get object: {str(e)}")
        return [TextContent(type="text", text=f"Failed to get object: {str(e)}")]


def get_parts_list(ctx: Context) -> list[TextContent]:
    """Get the list of parts in the parts library addon."""
    freecad = get_freecad_connection()
    parts = freecad.get_parts_list()
    if parts:
        return [TextContent(type="text", text=json.dumps(parts))]
    else:
        return [
            TextContent(
                type="text",
                text=f"No parts found in the parts library. You must add parts_library addon.",
            )
        ]


@mcp.prompt()
def asset_creation_strategy() -> str:
    return """
Asset Creation Strategy for FreeCAD MCP

When creating content in FreeCAD, always follow these steps:

0. Before starting any task, always use get_objects() to confirm the current state of the document.

1. Utilize the parts library:
   - Check available parts using get_parts_list().
   - If the required part exists in the library, use insert_part_from_library() to insert it into your document.

2. If the appropriate asset is not available in the parts library:
   - Create basic shapes (e.g., cubes, cylinders, spheres) using create_object().
   - Adjust and define detailed properties of the shapes as necessary using edit_object().

3. Always assign clear and descriptive names to objects when adding them to the document.

4. Explicitly set the position, scale, and rotation properties of created or inserted objects using edit_object() to ensure proper spatial relationships.

5. After editing an object, always verify that the set properties have been correctly applied by using get_object().

6. If detailed customization or specialized operations are necessary, use execute_code() to run custom Python scripts.

Only revert to basic creation methods in the following cases:
- When the required asset is not available in the parts library.
- When a basic shape is explicitly requested.
- When creating complex shapes requires custom scripting.
"""


def export_document_as_step(
    ctx: Context, doc_name: str, file_path: str
) -> list[TextContent]:
    """Exports all geometric objects from a document to a STEP file.
    This automatically finds all objects with a 'Shape' (3D geometry) and exports them.

    Args:
        doc_name: The name of the document to export.
        file_path: The absolute path (on the machine running FreeCAD) to save the .step file.

    Returns:
        A message indicating the success or failure of the export.

    Examples:
        If you want to export all geometry from "MyDocument" to "C:/temp/export.step":
        ```json
        {
            "doc_name": "MyDocument",
            "file_path": "C:/temp/export.step"
        }
        ```
    """
    freecad = get_freecad_connection()
    try:
        res = freecad.export_step(doc_name, file_path) # <-- 已移除 obj_names
        if res["success"]:
            return [
                TextContent(
                    type="text",
                    text=f"Successfully exported all objects to {res['file_path']}",
                )
            ]
        else:
            return [
                TextContent(
                    type="text", text=f"Failed to export STEP file: {res['error']}"
                )
            ]
    except Exception as e:
        logger.error(f"Failed to export STEP: {str(e)}")
        return [TextContent(type="text", text=f"Failed to export STEP: {str(e)}")]


def export_document_as_stl(
    ctx: Context, doc_name: str, file_path: str
) -> list[TextContent]:
    """Exports all geometric objects from a document to an STL file.
    This automatically finds all objects with a 'Shape' (3D geometry) and exports them.

    Args:
        doc_name: The name of the document to export.
        file_path: The absolute path (on the machine running FreeCAD) to save the .stl file.

    Returns:
        A message indicating the success or failure of the export.

    Examples:
        If you want to export all geometry from "MyDocument" to "/home/user/my_model.stl":
        ```json
        {
            "doc_name": "MyDocument",
            "file_path": "/home/user/my_model.stl"
        }
        ```
    """
    freecad = get_freecad_connection()
    try:
        res = freecad.export_stl(doc_name, file_path) # <-- 已移除 obj_names
        if res["success"]:
            return [
                TextContent(
                    type="text",
                    text=f"Successfully exported all objects to {res['file_path']}",
                )
            ]
        else:
            return [
                TextContent(
                    type="text", text=f"Failed to export STL file: {res['error']}"
                )
            ]
    except Exception as e:
        logger.error(f"Failed to export STL: {str(e)}")
        return [TextContent(type="text", text=f"Failed to export STL: {str(e)}")]


def save_document_as_fcstd(
    ctx: Context, doc_name: str, file_path: str
) -> list[TextContent]:
    """Saves the specified document to a .FCStd file.
    This saves the entire document in FreeCAD's native format.

    Args:
        doc_name: The name of the document to save.
        file_path: The absolute path (on the machine running FreeCAD) to save the .fcstd file.
                   If the extension is missing, '.fcstd' will be added.

    Returns:
        A message indicating the success or failure of the save operation.

    Examples:
        If you want to save "MyDocument" to "C:/temp/my_project.fcstd":
        ```json
        {
            "doc_name": "MyDocument",
            "file_path": "C:/temp/my_project.fcstd"
        }
        ```
    """
    freecad = get_freecad_connection()
    try:
        res = freecad.save_document(doc_name, file_path)
        if res["success"]:
            return [
                TextContent(
                    type="text",
                    text=f"Successfully saved document to {res['file_path']}",
                )
            ]
        else:
            return [
                TextContent(
                    type="text", text=f"Failed to save document: {res['error']}"
                )
            ]
    except Exception as e:
        logger.error(f"Failed to save document: {str(e)}")
        return [TextContent(type="text", text=f"Failed to save document: {str(e)}")]
    

def close_document(
    ctx: Context, doc_name: str
) -> list[TextContent]:
    """Closes the specified document in FreeCAD.

    Args:
        doc_name: The name of the document to close.

    Returns:
        A message indicating the success or failure of the close operation.

    Examples:
        If you want to close "MyDocument":
        ```json
        {
            "doc_name": "MyDocument"
        }
        ```
    """
    freecad = get_freecad_connection()
    try:
        res = freecad.close_document(doc_name)
        if res["success"]:
            return [
                TextContent(
                    type="text",
                    text=f"Successfully closed document '{res['document_name']}'",
                )
            ]
        else:
            return [
                TextContent(
                    type="text", text=f"Failed to close document: {res['error']}"
                )
            ]
    except Exception as e:
        logger.error(f"Failed to close document: {str(e)}")
        return [TextContent(type="text", text=f"Failed to close document: {str(e)}")]


def main():
    """Run the MCP server"""
    global _only_text_feedback
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--only-text-feedback", action="store_true", help="Only return text feedback"
    )
    args = parser.parse_args()
    _only_text_feedback = args.only_text_feedback
    logger.info(f"Only text feedback: {_only_text_feedback}")
    mcp.run()

if __name__ == "__main__":
    main()
