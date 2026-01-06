import json
import logging
import socket
import textwrap
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

# 未启用的可选分析工具
# @mcp.tool()
# def analyze_object(ctx: Context, doc_name: str, obj_name: str) -> list[TextContent]:
#     """
#     [SPATIAL AWARENESS TOOL]
#     Get detailed geometric analysis (BoundingBox, Center) of an object.
#     CRITICAL: Use this BEFORE placing new objects next to existing ones to prevent collisions.
    
#     Args:
#         doc_name: The document name.
#         obj_name: The object to analyze.
        
#     Returns:
#         JSON string containing 'BoundBox' (XMin, XMax, YMin, YMax, ZMin, ZMax, Width, Length, Height) and 'Center'.
#     """
#     fc = get_freecad_connection()
#     code = textwrap.dedent(f"""
#     import FreeCAD
#     import json
    
#     def run_analysis():
#         try:
#             doc = FreeCAD.getDocument("{doc_name}")
#             if not doc: return json.dumps({{"error": "Document not found"}})
            
#             obj = doc.getObject("{obj_name}")
#             if not obj: 
#                 # Try finding by label if name fails
#                 objs = doc.getObjectsByLabel("{obj_name}")
#                 if objs: obj = objs[0]
#                 else: return json.dumps({{"error": "Object '{obj_name}' not found"}})
            
#             # Ensure geometry is up to date
#             if hasattr(obj, "Shape") and not obj.Shape.isNull():
#                 bbox = obj.Shape.BoundBox
                
#                 # Get precise geometric data
#                 data = {{
#                     "Name": obj.Name,
#                     "Label": obj.Label,
#                     "BoundBox": {{
#                         "XMin": round(bbox.XMin, 3), "XMax": round(bbox.XMax, 3), "Width": round(bbox.XLength, 3),
#                         "YMin": round(bbox.YMin, 3), "YMax": round(bbox.YMax, 3), "Length": round(bbox.YLength, 3),
#                         "ZMin": round(bbox.ZMin, 3), "ZMax": round(bbox.ZMax, 3), "Height": round(bbox.ZLength, 3)
#                     }},
#                     "Center": [round(bbox.Center.x, 3), round(bbox.Center.y, 3), round(bbox.Center.z, 3)],
#                     "Placement": {{
#                         "Base": [round(obj.Placement.Base.x, 3), round(obj.Placement.Base.y, 3), round(obj.Placement.Base.z, 3)]
#                     }}
#                 }}
                
#                 # Try to extract primitive parameters if applicable (Radius, etc.)
#                 if hasattr(obj, "Radius"): data["Radius"] = obj.Radius
#                 if hasattr(obj, "Radius1"): data["Radius1"] = obj.Radius1
#                 if hasattr(obj, "Radius2"): data["Radius2"] = obj.Radius2
                
#                 return json.dumps(data)
#             else:
#                 return json.dumps({{"error": "Object has no shape geometry"}})
                
#         except Exception as e:
#             return json.dumps({{"error": str(e)}})

#     run_analysis()
#     """)
    
#     try:
#         res = fc.execute_code(code)
#         # execute_code result is usually in the 'message' or generic return depending on server implementation
#         # Assuming the server returns the string output of the script
#         if res.get("success"):
#             return [TextContent(type="text", text=res.get("message", "{}"))]
#         else:
#             return [TextContent(type="text", text=f"Analysis failed: {res.get('error')}")]
#     except Exception as e:
#         return [TextContent(type="text", text=f"Analysis failed: {str(e)}")]

@mcp.tool()
def create_primitive(
    ctx: Context, 
    doc_name: str, 
    primitive_type: Literal["Box", "Cylinder", "Sphere", "Cone", "Torus"], 
    name: str, 
    dimensions: dict[str, float],
    position: tuple[float, float, float] = (0.0, 0.0, 0.0),
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
) -> list[TextContent]:
    """
    Create a basic geometric primitive with position and rotation.
    """
    fc = get_freecad_connection()
    
    # --- [修复 1] 参数补全：防止 tuple index out of range ---
    # 无论 LLM 传几个数，我们强行补齐到 3 位
    p = list(position) + [0.0] * 3
    r = list(rotation) + [0.0] * 3
    
    # 1. 参数预校验
    required_keys = {
        "Box": ["Length", "Width", "Height"],
        "Cylinder": ["Radius", "Height"],
        "Sphere": ["Radius"],
        "Cone": ["Radius1", "Radius2", "Height"],
        "Torus": ["Radius1", "Radius2"]
    }
    
    missing = [k for k in required_keys.get(primitive_type, []) if k not in dimensions]
    if missing:
        return [TextContent(type="text", text=f"Error: Missing dimensions for {primitive_type}: {missing}")]

    # 2. 构建 Python 脚本
    code = textwrap.dedent(f"""
    import FreeCAD
    import Part
    from FreeCAD import Vector, Rotation

    try:
        doc = FreeCAD.getDocument("{doc_name}")
        if not doc: doc = FreeCAD.newDocument("{doc_name}")
        
        obj_name = "{name}"
        if doc.getObject(obj_name):
            doc.removeObject(obj_name)
        
        # 创建形状
        shape = None
        dims = {dimensions}
        
        if "{primitive_type}" == "Box":
            shape = Part.makeBox(dims["Length"], dims["Width"], dims["Height"])
        elif "{primitive_type}" == "Cylinder":
            shape = Part.makeCylinder(dims["Radius"], dims["Height"])
        elif "{primitive_type}" == "Sphere":
            shape = Part.makeSphere(dims["Radius"])
        elif "{primitive_type}" == "Cone":
            shape = Part.makeCone(dims["Radius1"], dims["Radius2"], dims["Height"])
        elif "{primitive_type}" == "Torus":
            shape = Part.makeTorus(dims["Radius1"], dims["Radius2"])
            
        if not shape:
            raise Exception("Failed to create shape geometry")

        # 创建对象
        obj = doc.addObject("Part::Feature", obj_name)
        obj.Shape = shape
        
        # 设置位置和旋转
        # [修复] 使用补齐后的 p[0], p[1], p[2]
        pos = Vector({p[0]}, {p[1]}, {p[2]})
        # 欧拉角转换
        rot = Rotation(Vector(1,0,0), {r[0]}) * Rotation(Vector(0,1,0), {r[1]}) * Rotation(Vector(0,0,1), {r[2]})
        
        obj.Placement = FreeCAD.Placement(pos, rot)
        
        # 可见性设置
        obj.ViewObject.Visibility = True
        doc.recompute()
        
        # [修复 2] 正确的 Headless 检查
        if FreeCAD.GuiUp:
            import FreeCADGui
            FreeCADGui.SendMsgToActiveView("ViewFit")
        
    except Exception as e:
        raise e
    """)
    try:
        res = fc.execute_code(code)
        if res.get("success"):
            return [TextContent(type="text", text=f"Success: Created {primitive_type} '{name}' at pos={position}, rot={rotation}")]
        else:
            return [TextContent(type="text", text=f"Failed: {res.get('error')}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Critical Error: {str(e)}")]


@mcp.tool()
def create_gear(
    ctx: Context, 
    doc_name: str, 
    teeth: int, 
    module: float, 
    name: str = "Gear", 
    thickness: float = 20.0, 
    pressure_angle: float = 20.0,
    bore_diameter: float = 0.0,
    keyway_width: float = 0.0,
    keyway_depth: float = 0.0,
    position: tuple[float, float, float] = (0.0, 0.0, 0.0),
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0) 
) -> list[TextContent]:
    """
    Create a complete industrial spur gear (Fixed Topology).
    """
    fc = get_freecad_connection()
    
    # --- [修复] 参数补全 ---
    p = list(position) + [0.0] * 3
    r = list(rotation) + [0.0] * 3
    
    code = textwrap.dedent(f"""
        import FreeCAD
        import Part
        import math
        from FreeCAD import Vector, Rotation

        def make_gear_fixed():
            try:
                # 1. 文档管理
                try:
                    doc = FreeCAD.getDocument("{doc_name}")
                except:
                    doc = None
                if not doc:
                    doc = FreeCAD.newDocument("{doc_name}")

                # 2. 齿轮参数
                Z = int({teeth})
                m = float({module})
                h = float({thickness})
                alpha = math.radians(float({pressure_angle}))
                
                # 3. 几何计算
                d_ref = m * Z
                r_ref = d_ref / 2.0
                r_base = r_ref * math.cos(alpha)
                ha = 1.0 * m
                hf = 1.25 * m
                r_tip = r_ref + ha
                r_root = r_ref - hf

                def get_point(t, r_b):
                    return r_b * (math.cos(t) + t * math.sin(t)), r_b * (math.sin(t) - t * math.cos(t))

                if r_tip > r_base:
                    t_max = math.sqrt((r_tip / r_base)**2 - 1)
                else:
                    t_max = 0.1
                    
                inv_alpha = math.tan(alpha) - alpha
                beta = (math.pi / (2 * Z)) + inv_alpha
                
                points = []
                res = 7
                
                # 4. 生成点集
                for i in range(Z):
                    theta_start = i * 2 * math.pi / Z
                    # --- 第一步：右齿面 ---
                    for s in range(res + 1):
                        t = (s / res) * t_max
                        x, y = get_point(t, r_base)
                        r = math.sqrt(x*x + y*y)
                        curr_inv = math.tan(math.acos(r_base/max(r, r_base))) - math.acos(r_base/max(r, r_base))
                        phi = theta_start - beta + curr_inv
                        px, py = r*math.cos(phi), r*math.sin(phi)
                        if r < r_root: px, py = r_root*math.cos(phi), r_root*math.sin(phi)
                        points.append(Vector(px, py, 0))
                    # --- 第二步：左齿面 ---
                    for s in range(res, -1, -1):
                        t = (s / res) * t_max
                        x, y = get_point(t, r_base)
                        r = math.sqrt(x*x + y*y)
                        curr_inv = math.tan(math.acos(r_base/max(r, r_base))) - math.acos(r_base/max(r, r_base))
                        phi = theta_start + beta - curr_inv
                        px, py = r*math.cos(phi), r*math.sin(phi)
                        if r < r_root: px, py = r_root*math.cos(phi), r_root*math.sin(phi)
                        points.append(Vector(px, py, 0))
                points.append(points[0]) 
                
                # 5. 生成实体
                gear_wire = Part.makePolygon(points)
                gear_face = Part.Face(gear_wire)
                gear_solid = gear_face.extrude(Vector(0, 0, h))

                # 6. 切割轴孔和键槽
                bore_d = {bore_diameter}
                kw_w = {keyway_width}
                kw_d = {keyway_depth}
                
                cutting_tools = []
                if bore_d > 0:
                    cutting_tools.append(Part.makeCylinder(bore_d / 2.0, h))
                    
                if kw_w > 0 and kw_d > 0 and bore_d > 0:
                    r_hole = bore_d / 2.0
                    box_w = kw_w
                    overlap = 0.1
                    keyway_box = Part.makeBox(box_w, kw_d * 2 + r_hole, h)
                    keyway_box.translate(Vector(-box_w/2.0, r_hole - overlap, 0))
                    cutting_tools.append(keyway_box)

                if cutting_tools:
                    if len(cutting_tools) > 1:
                        tool_solid = cutting_tools[0].multiFuse(cutting_tools[1:])
                    else:
                        tool_solid = cutting_tools[0]
                    final_solid = gear_solid.cut(tool_solid)
                else:
                    final_solid = gear_solid

                # 7. 清理与显示
                obj_name = "{name}"
                if doc.getObject(obj_name):
                    doc.removeObject(obj_name)
                    
                obj = doc.addObject("Part::Feature", obj_name)
                obj.Shape = final_solid
                
                # --- 直接设置位置和旋转 (使用补全参数) ---
                pos = Vector({p[0]}, {p[1]}, {p[2]})
                rot = Rotation(Vector(1,0,0), {r[0]}) * Rotation(Vector(0,1,0), {r[1]}) * Rotation(Vector(0,0,1), {r[2]})
                
                obj.Placement = FreeCAD.Placement(pos, rot)
                
                obj.ViewObject.Visibility = True
                doc.recompute()
                
                # [修复] Headless 安全检查
                if FreeCAD.GuiUp:
                    import FreeCADGui
                    FreeCADGui.SendMsgToActiveView("ViewFit")
                    
                return "Gear '" + obj_name + "' created."

            except Exception as e:
                return str(e)

        make_gear_fixed()
    """)
    res = fc.execute_code(code)
    
    if res.get("success"):
        return [TextContent(type="text", text=f"Success: Created Gear at {position}.")]
    else:
        return [TextContent(type="text", text=f"Error creating gear: {res.get('error')}")]

@mcp.tool()
def boolean_operation(
    ctx: Context,
    doc_name: str,
    operation: Literal["Cut", "Fuse", "Common", "Union", "Subtract"], 
    base_obj_name: str,
    tool_obj_names: list[str],
    result_obj_name: str = None
) -> list[TextContent]:
    """
    Perform a Boolean operation. 
    SMART LOOKUP: Tries to find objects by Name first, then by Label.
    """
    fc = get_freecad_connection()
    
    # 兼容同义词
    real_op = operation
    if operation == "Union": real_op = "Fuse"
    elif operation == "Subtract": real_op = "Cut"
        
    if not result_obj_name:
        result_obj_name = f"{real_op}_{base_obj_name}"

    code = textwrap.dedent(f"""
    import FreeCAD
    import Part
    
    def find_object(doc, name):
        # 1. 尝试通过内部 Name 获取
        obj = doc.getObject(name)
        if obj: return obj
        
        # 2. 尝试通过 Label 获取 (容错关键!)
        objs = doc.getObjectsByLabel(name)
        if objs: return objs[0] # 返回第一个匹配项
        
        return None

    try:
        doc = FreeCAD.getDocument("{doc_name}")
        if not doc: raise Exception("Document not found")

        # 使用智能查找
        base = find_object(doc, "{base_obj_name}")
        if not base: raise Exception("Base object '{base_obj_name}' not found (checked Name and Label)")

        tools = []
        for name in {repr(tool_obj_names)}:
            t = find_object(doc, name)
            if t: 
                tools.append(t)
            else:
                # 记录警告但继续，或者抛出异常
                print(f"Warning: Tool object '{{name}}' not found.")
        
        if not tools: raise Exception("No valid tool objects found")

        # 清理旧结果
        if doc.getObject("{result_obj_name}"):
            doc.removeObject("{result_obj_name}")

        op = "{real_op}"
        new_obj = None

        if op == "Fuse":
            new_obj = doc.addObject("Part::MultiFuse", "{result_obj_name}")
            new_obj.Shapes = [base] + tools
            
        elif op == "Cut":
            if len(tools) == 1:
                new_obj = doc.addObject("Part::Cut", "{result_obj_name}")
                new_obj.Base = base
                new_obj.Tool = tools[0]
            else:
                temp_name = "{result_obj_name}_Tools"
                if doc.getObject(temp_name): doc.removeObject(temp_name)
                
                tool_compound = doc.addObject("Part::MultiFuse", temp_name)
                tool_compound.Shapes = tools
                tool_compound.ViewObject.Visibility = False
                
                new_obj = doc.addObject("Part::Cut", "{result_obj_name}")
                new_obj.Base = base
                new_obj.Tool = tool_compound
                
        elif op == "Common":
            new_obj = doc.addObject("Part::Common", "{result_obj_name}")
            new_obj.Base = base
            new_obj.Tool = tools[0]

        # 隐藏输入
        base.ViewObject.Visibility = False
        for t in tools:
            t.ViewObject.Visibility = False
            
        doc.recompute()
        new_obj.ViewObject.Visibility = True
        
        if FreeCAD.GuiUp:
            import FreeCADGui
            FreeCADGui.SendMsgToActiveView("ViewFit")

    except Exception as e:
        raise e
    """)
    res = fc.execute_code(code)
    
    if res.get("success"):
        return [TextContent(type="text", text=f"Success: Boolean {real_op} created '{result_obj_name}'.")]
    else:
        return [TextContent(type="text", text=f"Failed: {res.get('error')}")]

@mcp.tool()
def fillet_object(
    ctx: Context,
    doc_name: str,
    obj_name: str,
    radius: float,
    result_obj_name: str = None, 
    edge_indices: list[int] = None,
    filter_orientation: Literal["All", "Vertical", "Horizontal_XY"] = "All"
) -> list[TextContent]:
    """Apply Fillet to an object."""
    fc = get_freecad_connection()
    
    if not result_obj_name:
        result_obj_name = f"Fillet_{obj_name}"

    code = textwrap.dedent(f"""
    import FreeCAD
    import Part

    try:
        doc = FreeCAD.getDocument("{doc_name}")
        obj = doc.getObject("{obj_name}")
        if not obj: raise Exception("Object not found")

        target_edges = []
        if {edge_indices} is not None:
            indices = {edge_indices}
            for i in indices:
                if 1 <= i <= len(obj.Shape.Edges):
                    target_edges.append(obj.Shape.Edges[i-1])
        else:
            filter_type = "{filter_orientation}"
            all_edges = obj.Shape.Edges
            for e in all_edges:
                bbox = e.BoundBox
                dx = bbox.XMax - bbox.XMin
                dy = bbox.YMax - bbox.YMin
                dz = bbox.ZMax - bbox.ZMin
                tol = 0.01
                is_vertical = (dz > tol) and (dx < tol) and (dy < tol)
                is_horizontal_xy = (dz < tol)
                
                if filter_type == "All": target_edges.append(e)
                elif filter_type == "Vertical" and is_vertical: target_edges.append(e)
                elif filter_type == "Horizontal_XY" and is_horizontal_xy: target_edges.append(e)

        if not target_edges: raise Exception("No edges found")

        new_shape = obj.Shape.makeFillet({radius}, target_edges)
        
        if doc.getObject("{result_obj_name}"):
            doc.removeObject("{result_obj_name}")

        new_obj = doc.addObject("Part::Feature", "{result_obj_name}")
        new_obj.Shape = new_shape
        
        obj.ViewObject.Visibility = False
        new_obj.ViewObject.Visibility = True
        
        doc.recompute()
        # [修复] Headless 安全检查
        if FreeCAD.GuiUp:
            import FreeCADGui
            FreeCADGui.SendMsgToActiveView("ViewFit")

    except Exception as e:
        raise e
    """)
    res = fc.execute_code(code)
    
    if res.get("success"):
        return [TextContent(type="text", text=f"Success: Fillet '{result_obj_name}' created.")]
    else:
        return [TextContent(type="text", text=f"Failed: {res.get('error')}")]

@mcp.tool()
def chamfer_object(
    ctx: Context,
    doc_name: str,
    obj_name: str,
    distance: float,
    result_obj_name: str = None,
    edge_indices: list[int] = None,
    filter_orientation: Literal["All", "Vertical", "Horizontal_XY"] = "All"
) -> list[TextContent]:
    """Apply a Chamfer to an object with smart edge filtering."""
    fc = get_freecad_connection()
    
    code = textwrap.dedent(f"""
    import FreeCAD
    import Part

    try:
        doc = FreeCAD.getDocument("{doc_name}")
        obj = doc.getObject("{obj_name}")
        if not obj: raise Exception("Object not found")

        target_edges = []
        
        if {edge_indices} is not None:
            indices = {edge_indices}
            for i in indices:
                if 1 <= i <= len(obj.Shape.Edges):
                    target_edges.append(obj.Shape.Edges[i-1])
        else:
            filter_type = "{filter_orientation}"
            all_edges = obj.Shape.Edges
            
            for e in all_edges:
                bbox = e.BoundBox
                dx = bbox.XMax - bbox.XMin
                dy = bbox.YMax - bbox.YMin
                dz = bbox.ZMax - bbox.ZMin
                tol = 0.01
                
                is_vertical = (dz > tol) and (dx < tol) and (dy < tol)
                is_horizontal_xy = (dz < tol)
                
                if filter_type == "All": target_edges.append(e)
                elif filter_type == "Vertical" and is_vertical: target_edges.append(e)
                elif filter_type == "Horizontal_XY" and is_horizontal_xy: target_edges.append(e)

        if not target_edges: raise Exception("No edges found")

        new_shape = obj.Shape.makeChamfer({distance}, target_edges)
        
        res_name = "{result_obj_name}" if "{result_obj_name}" != "None" else f"Chamfer_{{obj.Name}}"
        if doc.getObject(res_name):
            doc.removeObject(res_name)

        new_obj = doc.addObject("Part::Feature", res_name)
        new_obj.Shape = new_shape
        
        obj.ViewObject.Visibility = False
        new_obj.ViewObject.Visibility = True
        
        doc.recompute()
        # [修复] Headless 安全检查
        if FreeCAD.GuiUp:
            import FreeCADGui
            FreeCADGui.SendMsgToActiveView("ViewFit")

    except Exception as e:
        raise e
    """)
    res = fc.execute_code(code)
    
    if res.get("success"):
        return [TextContent(type="text", text=f"Success: Chamfer applied.")]
    else:
        return [TextContent(type="text", text=f"Failed: {res.get('error')}")]

@mcp.tool()
def create_polar_array(
    ctx: Context,
    doc_name: str,
    base_obj_name: str,
    num_copies: int,
    axis: Literal["Z", "X", "Y"] = "Z",
    center_x: float = 0.0,
    center_y: float = 0.0,
    center_z: float = 0.0,
    position: tuple[float, float, float] = (0.0, 0.0, 0.0),
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
) -> list[TextContent]:
    """
    Create a circular (polar) array of an object.
    """
    fc = get_freecad_connection()
    
    # [修复] 参数补全
    p = list(position) + [0.0] * 3
    r = list(rotation) + [0.0] * 3
    
    code = textwrap.dedent(f"""
    import FreeCAD
    import Draft
    from FreeCAD import Vector, Rotation

    try:
        doc = FreeCAD.getDocument("{doc_name}")
        base = doc.getObject("{base_obj_name}")
        
        if not base:
            raise Exception("Base object not found")
            
        axis_vec = Vector(0, 0, 1)
        if "{axis}" == "X": axis_vec = Vector(1, 0, 0)
        elif "{axis}" == "Y": axis_vec = Vector(0, 1, 0)
        
        center = Vector({center_x}, {center_y}, {center_z})
        
        array_name = f"Array_{{base.Name}}"
        array_obj = Draft.make_polar_array(
            base,
            number={num_copies},
            angle=360.0,
            center=center,
            use_link=False
        )
        array_obj.Label = array_name
        
        # [修复] 使用补齐后的参数
        pos = Vector({p[0]}, {p[1]}, {p[2]})
        rot = Rotation(Vector(1,0,0), {r[0]}) * Rotation(Vector(0,1,0), {r[1]}) * Rotation(Vector(0,0,1), {r[2]})
        array_obj.Placement = FreeCAD.Placement(pos, rot)
        
        doc.recompute()
        # [修复] Headless 安全检查
        if FreeCAD.GuiUp:
            import FreeCADGui
            FreeCADGui.SendMsgToActiveView("ViewFit")
        
    except Exception as e:
        raise e
    """)
    res = fc.execute_code(code)
    
    if res.get("success"):
        return [TextContent(type="text", text=f"Success: Created Polar Array of '{base_obj_name}' ({num_copies} copies).")]
    else:
        return [TextContent(type="text", text=f"Error creating array: {res.get('error')}")]

@mcp.tool()
def create_rectangular_array(
    ctx: Context,
    doc_name: str,
    base_obj_name: str,
    n_x: int,
    n_y: int,
    interval_x: float,
    interval_y: float,
    position: tuple[float, float, float] = (0.0, 0.0, 0.0), 
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0) 
) -> list[TextContent]:
    """
    Create a rectangular (orthogonal) array of an object.
    """
    fc = get_freecad_connection()
    
    # 参数补全
    p = list(position) + [0.0] * 3
    r = list(rotation) + [0.0] * 3

    code = textwrap.dedent(f"""
    import FreeCAD
    import Draft
    from FreeCAD import Vector, Rotation

    try:
        doc = FreeCAD.getDocument("{doc_name}")
        base = doc.getObject("{base_obj_name}")
        
        if not base:
            raise Exception("Base object not found")
            
        v_x = Vector({interval_x}, 0, 0)
        v_y = Vector(0, {interval_y}, 0)
        
        # [修改点] 统一命名规则：去掉 _Rect，和 create_polar_array 保持一致
        # 这样 Agent 只需要记住 "Array_" 前缀即可，不容易出错
        array_name = f"Array_{{base.Name}}"
        
        # 如果重名，先清理
        if doc.getObject(array_name):
            doc.removeObject(array_name)
        
        array_obj = Draft.make_ortho_array(
            base,
            v_x=v_x,
            v_y=v_y,
            n_x={n_x},
            n_y={n_y},
            use_link=False 
        )
        array_obj.Label = array_name
        
        # 设置阵列整体位置
        v_pos = Vector({p[0]}, {p[1]}, {p[2]})
        r_rot = Rotation(Vector(1,0,0), {r[0]}) * Rotation(Vector(0,1,0), {r[1]}) * Rotation(Vector(0,0,1), {r[2]})
        array_obj.Placement = FreeCAD.Placement(v_pos, r_rot)
        
        doc.recompute()
        
        if FreeCAD.GuiUp:
            import FreeCADGui
            FreeCADGui.SendMsgToActiveView("ViewFit")
        
    except Exception as e:
        raise e
    """)
    res = fc.execute_code(code)
    
    if res.get("success"):
        return [TextContent(type="text", text=f"Success: Created Rectangular Array of '{base_obj_name}' ({n_x}x{n_y}).")]
    else:
        return [TextContent(type="text", text=f"Error creating array: {res.get('error')}")]

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

    You are an expert CAD engineer. Follow this strictly ordered workflow to create reliable 3D models:

    PHASE 1: ANALYSIS & PREPARATION
    1.  **Context Check**: Always start by running `get_objects()` to understand the current document state and avoid naming collisions.
    2.  **Library Check**: Use `get_parts_list()` to see if a pre-made standard part exists. If yes, use `insert_part_from_library()`.

    PHASE 2: COMPONENT GENERATION (Primitives & Mechanical Parts)
    3.  **Basic Shapes**: When creating standard geometric shapes (Box, Cylinder, Sphere, Cone, Torus), **ALWAYS prefer `create_primitive`** over `create_object`.
        * **DIRECT POSITIONING**: Use the `position` (x,y,z) and `rotation` (x,y,z angles) parameters DIRECTLY in the tool call. **Do NOT use `edit_object` immediately after creation** to move the object; do it in one step.
    4.  **Mechanical Parts (Gears)**: Use `create_gear` for gears.
        * **GOLDEN RULE (ALL-IN-ONE)**: The `create_gear` tool is a SUPER TOOL.
            - **Features**: Set `bore_diameter`, `keyway_width`, and `keyway_depth` to cut features automatically.
            **CONTEXT AWARENESS (CRITICAL)**: Check if the assembly involves a Shaft or a Central Bore defined in previous steps. 
                - If the housing has a 35mm bore, the Gear sitting on top **MUST** also have a `bore_diameter=70` (Diameter, not Radius!) or matching size. 
                - **NEVER create a solid gear on top of a hollow housing.** Always add a bore.
            - **Placement**: Set `position` and `rotation` to place the gear correctly in 3D space immediately.
            - **FORBIDDEN**: Do NOT create separate Cylinders/Boxes and use Boolean Cut to make the hole/keyway manually.
            - **ONE STEP**: Generate the complete gear + hole + keyway + placement in a single tool call.
        * **Engineering Rule**: Meshing gears MUST share the exact same `module`.

    PHASE 3: CONSTRUCTION (Patterns & Booleans)
    5.  **Patterns (Array)**: 
        * Use `create_polar_array` for circular patterns (e.g., flanges).
        * Use `create_rectangular_array` for grids (e.g., 4 mounting holes on corners). 
        - Tip: Create the bottom-left object first, then array it with X/Y intervals.
        * **Efficiency**: Use `position`/`rotation` in array tools to place the entire pattern correctly in one step.
        * **Positioning Rule (CRITICAL)**: When placing mounting holes near corners on a Chamfered plate:
            - Coordinate limit = (PlateWidth/2) - ChamferSize - HoleRadius - Margin(5mm).
            - Example: For 200x200 plate with 30mm chamfer and 8mm hole:
              Max Coord = 100 - 30 - 8 - 5 = 57mm.
            - **DO NOT** place holes at [80, 80] for a 30mm chamfer! They will break the edge. Place them at **[60, 60]** or less.
        
    6.  **Combine & Cut**: Use `boolean_operation` to build complex geometry.
        * Use 'Fuse' to combine shapes.
        * Use 'Cut' to remove material (e.g., a cylinder cutting a hole in a box).
        * **Note**: This operation happens **IN-PLACE**. Ensure inputs are positioned correctly **before** calling this.

    PHASE 4: DETAILING (Finishing Touches)
    7.  **Edge Treatment**: Apply fillets and chamfers **LAST**, after the main shape is finalized.
        * **Smart Selection**: Use `filter_orientation` (e.g., "Vertical", "Horizontal_XY") to auto-select edges.
        * **Note**: These operations happen **IN-PLACE**. Do not attempt to move the object during this step.

    PHASE 5: VERIFICATION
    8.  **Verify**: After major operations, use `get_object()` to verify properties or request a screenshot via `get_view()`.

    FALLBACK:
    9.  **Custom Scripting**: Only use `execute_code` for:
        * Complex shapes not covered by primitives (e.g., Lofts, Sweeps).
        * Advanced mathematical surfaces.
        * Debugging specific errors.
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
