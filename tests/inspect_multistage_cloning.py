import os
import re
import sys

from msim_compiler import MS14Codec, get_logger

def inspect_topology_for_stage_cloning() -> None:
    logger = get_logger("STAGE-CLONER")
    codec = MS14Codec()
    tpl_path = "D:/msim_binary_fuzzer/msim_compiler/templates/base_sallen_key.ms14"
    xml_text = codec.decode_to_string(tpl_path)

    nodes = re.findall(r'<Item\s+CiID="(\d+)"\s+Class="CiNode">(.*?)</Item>\s*(?=<Item\s+CiID=|\s*</Elements>)', xml_text, re.DOTALL)
    logger.info(f"Discovered {len(nodes)} electrical <CiNode> nets in template.")

    comp_map = {}
    for cm in re.finditer(r'<Item\s+CiID="(\d+)"\s+Class="CiComponent">\s*<CiComponent\s+[^>]*LocalName="&amp;ASC([^"]+)"[^>]* SymCompID="(\d+)"', xml_text):
        ci_id, cname, sym_id = cm.groups()
        comp_map[ci_id] = (cname, sym_id)

    for n_id, n_body in nodes[:15]:
        ports = re.findall(r'<CiPort\s+[^>]*Component="(\d+)"\s+[^>]*Name="&amp;ASC([^"]+)"', n_body)
        if not ports:
            ports = re.findall(r'Component="(\d+)"', n_body)
            port_desc = [f"{comp_map.get(cid, (cid, ''))[0]}" for cid in ports]
        else:
            port_desc = [f"{comp_map.get(cid, (cid, ''))[0]}.{pname}" for cid, pname in ports]
        logger.info(f"  Net CiID={n_id:<10} connects: {port_desc}")

    for target_ref in ["R1", "U2", "XSC2", "XSC3", "XSC4"]:
        m = re.search(r'<Item\s+CiID="(\d+)"\s+Class="CiComponent">\s*<CiComponent\s+[^>]*LocalName="&amp;ASC' + target_ref + r'"[^>]*>(.*?)</CiComponent>', xml_text, re.DOTALL)
        if m:
            cid, body = m.groups()
            tmplt = re.findall(r'String="(&amp;ASC[^"]+)"', body)
            logger.info(f"  [{target_ref}] (CiID={cid}) Templates={tmplt[:2]}")

    fig9_path = "D:/automation3/Lab1_Active_Filters/Figure_9/Figure_9.ms14"
    if os.path.exists(fig9_path):
        f9_xml = codec.decode_to_string(fig9_path)
        f9_comps = re.findall(r'LocalName="&amp;ASC([^"]+)"', f9_xml)
        logger.info(f"Found 3-OpAmp Circuit Figure_9.ms14 -> Components ({len(f9_comps)}): {f9_comps}")

if __name__ == "__main__":
    inspect_topology_for_stage_cloning()
