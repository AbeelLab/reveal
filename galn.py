#!/usr/bin/env python
"""
Created on Thu Oct  2 13:26:24 2014

@author: jasperlinthorst
"""

import math
import logging
import os
import argparse
import gzip
import pickle
import seqalign
import xmlrpclib
import uuid
import glob
import random
import colorsys
import sys

#def group_mums(mums):
    
    

def check_colinearity(mums):
    matches=dict()
    for i,m in enumerate(mums):
        n1=m[3]
        n2=m[4]
        n1start=m[0]
        n2start=m[1]
        l=m[2]
        n1end=n1start+l
        n2end=n2start+l
        rc=m[5]
        if matches.has_key(n1):
            matches[n1].append(((n1start,n1end),(n2,n2start,n2end,i)))
        else:
            matches[n1]=[((n1start,n1end),(n2,n2start,n2end,i))]
        if matches.has_key(n2):
            matches[n2].append(((n2start,n2end),(n1,n1start,n1end,i)))
        else:
            matches[n2]=[((n2start,n2end),(n1,n1start,n1end,i))]
    
    for v in matches.keys(): #for every node check if MUMS are colinear
        mv=sorted(matches[v],key=lambda match: match[0][0]) #sort by start in v
        v1e=mv[0][0][0]
        v2=mv[0][1][0]
        v2e=mv[0][1][2]
        p=set()
        for m in mv[1:]:
            print m
            start1=m[0][0]
            end1=m[0][1]
            start2=m[1][1]
            end2=m[1][2]
            node=m[1][0]
            if m[1][0] in p:
                raise Exception("1 No colinearity in seeds, use larger k for seeding the alignment.")
            if start1<v1e:
                raise Exception("2 No colinearity in seeds, use larger k for seeding the alignment.")
            if node!=v2: #different node?
                p.add(v2)
                v2=m[1][0]
                v1e=end1
                v1b=start1
                v2e=end2
                v2b=start2
                continue
            if start2<v2e:
                raise Exception("3 No colinearity in seeds, use larger k for seeding the alignment. %s %s",v2e,m[1][1])
            v1e=m[0][1]
            v2e=m[1][2]
    return True


def cluster_contigs(index, minmum=1):
    #index=GSA.index('JJZE01.1.fsa_nt_JLAX01.1.fsa_nt.gfasta.gz','../../data/TBC/JJUZ01.1.fsa_nt',1)
    import networkx
    mums=index.get_mums(minmum) #get mums!
    
    #use networkx to recreate the graph, so its easy to extract the connected components
    g1=networkx.Graph()
    for v in index.graph.vertices.values():
        g1.add_node(v,seqlen=v.contig_end-v.contig_start)
    for e in index.graph.edges:
        g1.add_edge(e.source, e.target)
    
    g=networkx.Graph() #graph where connected components are nodes
    mapping={}
    for i,c in enumerate(networkx.connected_components(g1)):
        for n in c: #for every node in the connected component
            mapping[n]=i
        g.add_node(i,contigs=c,totalseq=sum([v.contig_end-v.contig_start for v in c]))
    
    print "Initial graph consists of",i,"connected components"
    
    for mum in mums:
        #lookup the connected components for a mum
        cc1=mapping[mum[3]]
        cc2=mapping[mum[4]]
        if not(g.has_edge(cc1,cc2)):
            g.add_edge(cc1,cc2,weight=mum[2],mums=[mum],n=1)
        else:
            g[cc1][cc2]['weight']+=mum[2]
            g[cc1][cc2]['n']+=1
            g[cc1][cc2]['mums'].append(mum)
    
    #filter out repetitive nodes
    
    remove=[]
    for e in g.edges(data=True):
        if e[2]['weight']<10000:
            remove.append((e[0],e[1]))
    
    for e in remove:
        g.remove_edge(e[0],e[1])
    
    nc=0
    for c in networkx.connected_components(g): #again extract connected components
        if len(c)>1:
            #extract from rindex and add to graph_aln stack
            vertices_to_extract=set()
            for k in c:
                for v in g.node[k]['contigs']:
                    vertices_to_extract.add(v)
            yield vertices_to_extract
            nc+=1
    
    print "Grouped inputs into",nc,"connected components"


def align(g, v1, aFrom, aTo, v2, bFrom, bTo, rcmatch, T, coordsystem=None):
        
        assert(v1.contig_start>=0)
        assert(v2.contig_start>=0)
        assert(aFrom<aTo)
        assert(bFrom<bTo)
        
        v1prefixlength=aFrom-v1.saoffset
        v1matchlength=aTo-aFrom
        v1suffixlength=v1.saoffset+(v1.contig_end-v1.contig_start)-aTo
        
        v2prefixlength=bFrom-v2.saoffset
        v2matchlength=bTo-bFrom
        v2suffixlength=v2.saoffset+(v2.contig_end-v2.contig_start)-bTo
        
        assert(v1.indexed==0)
        
        mergedv=g.add_vertex(2, v1.indexed, v1.coord_origin, v1.coord_contig, \
                                v1.contig_start+(aFrom-v1.saoffset), \
                                v1.contig_start+(aTo-v1.saoffset), \
                                aFrom, aFrom)
        
        for v in v1.origin.union(v2.origin):
            mergedv.origin.add(v)
        for v in v1.contig_origin.union(v2.contig_origin):
            mergedv.contig_origin.add(v)
        
        #TODO: add extract by domain so we dont have to create this node in the first place
        tmpv=g.add_vertex(2, v2.indexed, v2.coord_origin, v2.coord_contig, \
                        v2.contig_start+(bFrom-v2.saoffset), \
                        v2.contig_start+(bTo-v2.saoffset), \
                        bFrom, v2.rcsaoffset+(v2.saoffset+(v2.contig_end-v2.contig_start))-bTo if v2.rcsaoffset>v2.saoffset else bFrom)
        
        l1nv, r1nv, l2nv, r2nv = None, None, None, None
        
        assert(v1.contig_end-v1.contig_start==v1prefixlength+v1matchlength+v1suffixlength)
        
        #create a vertex for the unaligned prefix of v1
        #assert(v1.contig_start<=aFrom-v1.saoffset)
        if aFrom!=v1.saoffset: 
            l1nv=g.add_vertex(v1.input_origin, v1.indexed, v1.coord_origin, v1.coord_contig, \
                                v1.contig_start, \
                                v1.contig_start+(aFrom-v1.saoffset), \
                                v1.saoffset, v1.saoffset)
            for v in v1.origin:
                l1nv.origin.add(v)
            for v in v1.contig_origin:
                l1nv.contig_origin.add(v)

            for e in v1.edges_to:
                if e.orientation==0:
                    g.add_edge(e.source, l1nv, e.orientation) #reconnect the vertex
                else:
                    if e.target==v1:
                        g.add_edge(e.source, l1nv, e.orientation)
                    else:
                        g.add_edge(l1nv, e.target, e.orientation)
            g.add_edge(l1nv, mergedv, 0)
        else: #if no new vertex nescessary reconnect to the old neighbors
            for e in v1.edges_to:
                if e.orientation==0:
                    g.add_edge(e.source, mergedv, e.orientation) # same=0, inny=1, outty=2
                else:
                    if e.source==v1:
                        g.add_edge(mergedv, e.target, e.orientation) # same=0, inny=1, outty=2
                    else:
                        g.add_edge(e.source, mergedv, e.orientation) # same=0, inny=1, outty=2
        
        #create a vertex for the unaligned suffix of v1
        assert(aTo-v1.saoffset<=v1.contig_end)
        if aTo!=v1.saoffset+(v1.contig_end-v1.contig_start):
            
            r1nv=g.add_vertex(v1.input_origin, v1.indexed, v1.coord_origin, v1.coord_contig, \
                    v1.contig_start+v1prefixlength+v1matchlength, \
                    v1.contig_start+v1prefixlength+v1matchlength+v1suffixlength, \
                    v1.saoffset+v1prefixlength+v1matchlength, v1.saoffset+v1prefixlength+v1matchlength)
            for v in v1.origin:
                r1nv.origin.add(v)
            for v in v1.contig_origin:
                r1nv.contig_origin.add(v)
            
            for e in v1.edges_from:
                if e.orientation==0:
                    g.add_edge(r1nv, e.target, e.orientation) #reconnect the vertex
                else:
                    if e.source==v1:
                        g.add_edge(r1nv, e.target, e.orientation) # same=0, inny=1, outty=2
                    else:
                        g.add_edge(e.source, r1nv, e.orientation) # same=0, inny=1, outty=2
            g.add_edge(mergedv, r1nv, 0)
        else:
            for e in v1.edges_from:
                if e.orientation==0:
                    g.add_edge(mergedv, e.target, e.orientation)
                else:
                    if e.source==v1:
                        g.add_edge(mergedv, e.target, e.orientation) # same=0, inny=1, outty=2
                    else:
                        g.add_edge(e.source, mergedv, e.orientation) # same=0, inny=1, outty=2


        #create a vertex for the unaligned prefix of v2
        if bFrom!=v2.saoffset:
            l2nv=g.add_vertex(v2.input_origin, v2.indexed, v2.coord_origin, v2.coord_contig, \
                                v2.contig_start, \
                                v2.contig_start+(bFrom-v2.saoffset), \
                                v2.saoffset, v2.rcsaoffset+v2suffixlength+v2matchlength if v2.rcsaoffset>v2.saoffset else v2.saoffset)
            for v in v2.origin:
                l2nv.origin.add(v)
            for v in v2.contig_origin:
                l2nv.contig_origin.add(v)
            
            for e in v2.edges_to:
                if e.orientation==0:
                    g.add_edge(e.source, l2nv, e.orientation) #reconnect the vertex
                else:
                    if e.target==v2:
                        g.add_edge(e.source, l2nv, e.orientation)
                    else:
                        g.add_edge(l2nv, e.target, e.orientation)
            if rcmatch:
                g.add_edge(l2nv, mergedv, 1) #inny --><--
            else:
                g.add_edge(l2nv, mergedv, 0)
        else:
            for e in v2.edges_to:
                if rcmatch: #flip orientations of the edges
                    if e.orientation==0:
                        g.add_edge(e.source, mergedv, 1)
                    else:
                        if e.source==v2:
                            g.add_edge(mergedv, e.target, 0)
                        else:
                            g.add_edge(mergedv, e.source, 0)
                else:
                    if e.orientation==0:
                        g.add_edge(e.source, mergedv, e.orientation)
                    else: #source and target have no meaning
                        if e.source==v2:
                            g.add_edge(mergedv, e.target, e.orientation)
                        else:
                            g.add_edge(e.source, mergedv, e.orientation)
        
        #create a vertex for the unaligned suffix of v2
        assert(bTo-v2.saoffset<=v2.contig_end)
        if bTo!=v2.saoffset+(v2.contig_end-v2.contig_start):
            r2nv=g.add_vertex(v2.input_origin, v2.indexed, v2.coord_origin, v2.coord_contig, \
                    v2.contig_start+v2prefixlength+v2matchlength, \
                    v2.contig_start+v2prefixlength+v2matchlength+v2suffixlength, \
                    v2.saoffset+v2prefixlength+v2matchlength, v2.rcsaoffset)
            for v in v2.origin:
                r2nv.origin.add(v)
            for v in v2.contig_origin:
                r2nv.contig_origin.add(v)
            
            for e in v2.edges_from:
                if e.orientation==0:
                    assert(e.target!=r2nv)
                    g.add_edge(r2nv, e.target, e.orientation) #reconnect the vertex
                else:
                    if e.source==v2:
                        g.add_edge(r2nv, e.target, e.orientation)
                    else:
                        g.add_edge(e.source, r2nv, e.orientation)
            if rcmatch:
                g.add_edge(mergedv, r2nv, 2) #outty <-- -->
            else:
                g.add_edge(mergedv, r2nv, 0)
        else:
            for e in v2.edges_from:
                if rcmatch:
                    if e.orientation==0:
                        g.add_edge(mergedv, e.target, 2)
                    else:
                        if e.source==v2:
                            g.add_edge(e.target, mergedv, 0)
                        else:
                            g.add_edge(e.source, mergedv, 0)
                else:
                    if e.orientation==0:
                        g.add_edge(mergedv, e.target, e.orientation)
                    else:
                        if e.source==v2:
                            g.add_edge(mergedv, e.target, e.orientation)
                        else:
                            g.add_edge(e.source, mergedv, e.orientation)
        
        g.remove_vertex(v1)
        g.remove_vertex(v2)
        
        return l1nv, r1nv, l2nv, r2nv, mergedv, tmpv


def search(g, startv, set_of_nodes_with_false_condition, maxdegree=None, idirection=True, alg='bfs', condition=lambda s,e: True):
    '''
        Returns an iterator that does a search through the 
        graph, starting at vertex start. Direction specifies whether it
        traverses edges in normal or reversed order. Condition is called
        for every discovered vertex. When it evaluates to False, search
        from this vertex on is stopped. Alg keyword can be used to specify 
        depth-first search ('dfs'), default is breadth-first ('bfs').
    '''
    explored=set()
    stack=list()
    
    if idirection:
        for e in startv.edges_from:
            if e.orientation==0:
                if condition(startv, e.target):
                    if not((e.target,idirection) in explored):
                        explored.add((e.target,idirection))
                        stack.append((e.target,idirection,1))
                else:
                    set_of_nodes_with_false_condition.add((e.target,True))
            else:
                assert(e.orientation==1)
                if e.target==startv:
                    if condition(startv, e.source):
                        if not((e.source,not(idirection)) in explored):
                            explored.add((e.source,not(idirection)))
                            stack.append((e.source,not(idirection),1))
                    else:
                        set_of_nodes_with_false_condition.add((e.source,False))
                else:
                    if condition(startv, e.target):
                        if not((e.target,not(idirection)) in explored):
                            explored.add((e.target,not(idirection)))
                            stack.append((e.target,not(idirection),1))
                    else:
                        set_of_nodes_with_false_condition.add((e.target,False))
    else:
        for e in startv.edges_to:
            if e.orientation==0:
                if condition(startv, e.source):
                    if not((e.source,idirection) in explored):
                        explored.add((e.source,idirection))
                        stack.append((e.source,idirection,1))
                else:
                    set_of_nodes_with_false_condition.add((e.source,True))
            else:
                assert(e.orientation==2)
                if e.source==startv:
                    if condition(startv, e.target):
                        if not((e.target,not(idirection)) in explored):
                            explored.add((e.target,not(idirection)))
                            stack.append((e.target,not(idirection),1))
                    else:
                        set_of_nodes_with_false_condition.add((e.target,False))
                else:
                    if condition(startv, e.source):
                        if not((e.source,not(idirection)) in explored):
                            explored.add((e.source,not(idirection)))
                            stack.append((e.source,not(idirection),1))
                    else:
                        set_of_nodes_with_false_condition.add((e.source,False))
    
    while len(stack)>0:
        if alg=='dfs':
            v,direction,depth=stack.pop()
        else: #bfs
            v,direction,depth=stack.pop(0)
        
        yield v,direction==idirection
        
        if maxdegree!=None and depth==maxdegree:
            continue
        
        logging.log(1,"BFS Vertex: %s",v.id)
        
        if direction:
            for e in v.edges_from:
                if e.orientation==0:
                    if condition(startv, e.target):
                        if not((e.target,direction) in explored):
                            explored.add((e.target,direction))
                            stack.append((e.target,direction,depth+1))
                    else:
                        set_of_nodes_with_false_condition.add((e.target,direction==idirection))
                else:
                    if e.target==v:
                        if condition(startv, e.source):
                            if not((e.source,not(direction)) in explored):
                                explored.add((e.source,not(direction)))
                                stack.append((e.source,not(direction),depth+1))
                        else:
                            set_of_nodes_with_false_condition.add((e.source,not(direction)==idirection))
                    else:
                        if condition(startv, e.target):
                            if not((e.target,not(direction)) in explored):
                                explored.add((e.target,not(direction)))
                                stack.append((e.target,not(direction),depth+1))
                        else:
                            set_of_nodes_with_false_condition.add((e.target,not(direction)==idirection))
        else:
            for e in v.edges_to:
                if e.orientation==0:
                    if condition(startv, e.source):
                        if not((e.source,direction) in explored):
                            explored.add((e.source,direction))
                            stack.append((e.source,direction,depth+1))
                    else:
                        set_of_nodes_with_false_condition.add((e.source,direction==idirection))
                else:
                    if e.target==v:
                        if condition(startv, e.source):
                            if not((e.source,not(direction)) in explored):
                                explored.add((e.source,not(direction)))
                                stack.append((e.source,not(direction),depth+1))
                        else:
                            set_of_nodes_with_false_condition.add((e.source,not(direction)==idirection))
                    else:
                        if condition(startv, e.target):
                            if not((e.target,not(direction)) in explored):
                                explored.add((e.target,not(direction)))
                                stack.append((e.target,not(direction),depth+1))
                        else:
                            set_of_nodes_with_false_condition.add((e.target,not(direction)==idirection))

def iscommon(s,v):
    return not(s.origin.issubset(v.origin))


#NEED TO KNOW ALL SAMPLES IN THE GRAPH!
def bubbles(g,T,outputfilename,compress=True, minvarsize=0, maxvarsize=None, minnwsize=10, maxnwsize=10000):
    if compress:
        filename=outputfilename+'.vcf.gz'
        vcffile=gzip.open(filename,'wb')
    else:
        filename=outputfilename+'.vcf'
        vcffile=open(filename,'w')
    
    vcffile.write("##fileformat=VCFv4.1\n")
    vcffile.write("##INFO=<ID=SOURCE,Number=1,Type=String,Description=\"Id of the source node in the alignment graph\">\n")
    vcffile.write("##INFO=<ID=SOURCESIZE,Number=1,Type=Integer,Description=\"Length of the sequence defined on the source node.\">\n")
    vcffile.write("##INFO=<ID=SINK,Number=1,Type=String,Description=\"Id of the sink node in the alignment graph\">\n")
    vcffile.write("##INFO=<ID=SINKSIZE,Number=1,Type=Integer,Description=\"Length of the sequence defined on the sink node.\">\n")
    vcffile.write("##INFO=<ID=VARSIZE,Number=1,Type=Integer,Description=\"Max size of the variation in basepairs.\">\n")
    vcffile.write("##INFO=<ID=SV,Number=1,Type=Integer,Description=\"Whether the variant is considered a structural variant (e.g. VARSIZE>=50).\">\n")
    vcffile.write("##INFO=<ID=INDEL,Number=1,Type=Integer,Description=\"Whether the variant is considered an indel.\">\n")
    vcffile.write("##INFO=<ID=INSERT,Number=1,Type=Integer,Description=\"Whether the variant is considered an insertion.\">\n")
    vcffile.write("##INFO=<ID=DELETE,Number=1,Type=Integer,Description=\"Whether the variant is considered a deletion.\">\n")
    vcffile.write("##INFO=<ID=INVERSION,Number=1,Type=Integer,Description=\"Whether the variant is considered an inversion.\">\n")
    vcffile.write("##INFO=<ID=ALTCONTIGS,Number=1,Type=String,Description=\"The contigs in which the altervative alleles were observed.\">\n")
    vcffile.write("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t")
    origins=sorted(g.origins)
    for origin in origins:
        vcffile.write(origin+"\t")
    vcffile.write("\n")
    
    sourcesinkpairs=set()
    nsvs=0
    ninversions=0
    ngaps=0
    nvars=0
    
    for v in g.vertices.values():
        for direction in [True,False]:
            if (len(v.edges_from)>1 and direction==True) or (len(v.edges_to)>1 and direction==False): #at every split in the graph
                detected_join_nodes=set()
                bfsiter=search(g, v, detected_join_nodes, idirection=direction, condition=iscommon)
                bubble=set()
                for bv,o in bfsiter:
                    bubble.add(bv)
                if len(detected_join_nodes)==1: #we found a bubble! .. or a tip!
                    x=detected_join_nodes.pop()
                    jn=x[0]
                    jn_sameorientation=x[1]
                    ssp=tuple(sorted((jn.id,v.id)))
                    if ssp in sourcesinkpairs: #if we already found this combination of source and sink nodes, continue
                        continue
                    else:
                        sourcesinkpairs.add(ssp)
                    
                    detected_join_nodes=set()
                    bfsiter=search(g, jn, detected_join_nodes, idirection=not(direction) if jn_sameorientation else direction, condition=iscommon)
                    rbubble=set()
                    for bv,o in bfsiter:
                        rbubble.add(bv)
                    
                    if bubble!=rbubble or detected_join_nodes.pop()[0]!=v:
                        #not a clean bubble, probably tip!
                        continue
                    
                    chrom=v.coord_contig
                    
                    source=[jn,v][[jn.contig_end,v.contig_end].index(min([jn.contig_end,v.contig_end]))]
                    pos=min([jn.contig_end,v.contig_end])+1
                    assert(source.contig_end+1==pos)
                    
                    variantid='.'
                    #determine number of reference alleles in bubble
                    refalleles=0
                    refVertex=None
                    altVertices=[]
                    edges=set()
                    origins_in_bubble=source.origin.intersection(jn.origin)

                    for bv in bubble:
                        if v.coord_origin in bv.origin: #determine reference allele
                            refalleles+=1
                            refVertex=bv
                        else:
                            altVertices.append(bv)
                        edges=edges.union(bv.edges_from).union(bv.edges_to)
                        origins_in_bubble=origins_in_bubble.union(bv.origin)
                    
                    if len(edges)!=(len(bubble)*2): #in a simple bubble, there are always twice as many edges as nodes
                        logging.error("Complex bubble detected at %s, no call made in vcf.",pos)
                        continue
                    
                    indel=False
                    insert=False
                    delete=False
                    pc=T[source.saoffset+(source.contig_end-source.contig_start)-1].upper()
                    if refalleles==1:
                        ref=T[refVertex.saoffset:refVertex.saoffset+(refVertex.contig_end-refVertex.contig_start)].upper()
                        if len(altVertices)>0:
                            alt=','.join([T[a.saoffset:a.saoffset+(a.contig_end-a.contig_start)] for a in altVertices]).upper()
                        else:
                            ref=pc+ref #delete wrt reference
                            alt=pc
                            indel=True
                            delete=True
                    elif refalleles==0: #insert wrt reference
                        ref=pc
                        alt=','.join([pc+T[a.saoffset:a.saoffset+(a.contig_end-a.contig_start)] for a in altVertices]).upper()
                        indel=True
                        insert=True
                    else:
                        #complex bubble, or reference is heterozygous so can't determine one reference allele
                        #TODO: for the future consider how to handle heterozygous reference calls!?
                        ref='.'
                        alt='.'
                        logging.error("Complex bubble detected at %s, no call made in vcf.",pos)
                        continue
                    
                    if len(altVertices)>0:
                        varsize=abs(max([a.contig_end-a.contig_start for a in altVertices])-len(ref))
                    else:
                        varsize=len(ref)
                    
                    if varsize<minvarsize:
                        logging.error("Skipping variant at position %s with size %s, too small.",pos, varsize)
                        continue

                    if maxvarsize!=None and varsize>maxvarsize:
                        logging.error("Skipping variant at position %s with size %s, too big.",pos, varsize)
                        continue
                    
                    if ref.count("N")>10000: #TODO: ucsc gap track has only annotates gaps larger than 10000 (smaller ones do exist!)
                        gap=1
                        ngaps+=1
                    else:
                        gap=0
                    
                    if (len(ref)>minnwsize and len(ref)<maxnwsize) and (len(altVertices)==1 and (len(alt)>minnwsize and len(alt)<maxnwsize)):
                        inversionscore=seqalign.nw_align(ref,revcomp(alt))[2] - seqalign.nw_align(ref,alt)[2] #TODO: only works for one alt allele!
                    else:
                        inversionscore=0
                    
                    if inversionscore>min([len(ref),len(alt)]): #TODO: only works for one alt allele!
                        inversion=1
                        ninversions+=1
                    else:
                        inversion=0
                    
                    if varsize>=50:
                        sv=1
                        nsvs+=1
                    else:
                        sv=0
                    
                    nvars+=1
                    altcontigs=",".join([",".join([contig for contig in v.contig_origin]) for av in altVertices])
                    qual='.'
                    filt='.'
                    info='SOURCE={};SINK={};SOURCESIZE={};SINKSIZE={};VARSIZE={};SV={};INDEL={};INSERT={};DELETE={};INVERSION={};INVERSION_SCORE={};ALTCONTIGS={}'.format(v.id,jn.id,v.contig_end-v.contig_start,jn.contig_end-jn.contig_start,varsize,sv, (1 if indel else 0),(1 if insert else 0),(1 if delete else 0), inversion, inversionscore, altcontigs)
                    form='GT'
                    #CHROM	POS	ID	REF	ALT	QUAL	FILTER	INFO	FORMAT	  101-009-F
                    vcffile.write("{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}".format(chrom, pos, variantid, ref, alt, qual, filt, info, form))
                    
                    calls=""
                    for origin in origins:
                        call="."
                        alleles=[]
                        
                        for i,altV in enumerate(altVertices):
                            if origin in altV.origin:
                                alleles.append(str(i+1))
                        
                        if not(indel): #substitution
                            if (origin in refVertex.origin):
                                alleles.append('0')
                            if len(alleles)>0:
                                call="/".join(sorted(alleles))
                        elif insert: #insert
                            if (origin in jn.origin) and (origin in v.origin):
                                if len(alleles)>0:
                                    call="/".join(sorted(alleles))
                                else:
                                    call='0'
                        else: #deletion
                            assert(delete)
                            if (origin in jn.origin) and (origin in v.origin) and (origin not in refVertex.origin):
                                call='1'
                            else:
                                call='0'
                        
                        calls+="\t"+call
                    vcffile.write(calls+"\n")
                    
                    #logging.debug("found bubble starting splitting at: %s and joining at %s --> %s.", v.id, jn[0].id, [s[0].id for s in bubble])
                    #if len(bubble)==1:
                    #    indelnode=bubble.pop()
                    #    if (indelnode[0].contig_end-indelnode[0].contig_start)>50:
                    #        logging.info("found large insert (%s) at position: %s with length: %s", indelnode[0].id, min([v.contig_end,jn.contig_end]), indelnode[0].contig_end-indelnode[0].contig_start)
    vcffile.close()
    
    return filename, nvars, ngaps, ninversions, nsvs
    
def plot(g, keys=[], addseq=False):
    uid=uuid.uuid4().get_hex()[0:5]
    
    if keys==[]:
        keys=g.vertices.keys()
    
    nodes=[str(x)+"_"+uid for x in keys]
    
    server = xmlrpclib.ServerProxy("http://localhost:9000")
    networkid = server.Cytoscape.createNetwork('tmp')
    server.Cytoscape.createNodes(networkid, nodes)

    edges_from=[]
    edges_to=[]
    edges_orientations=[]
    
    for e in g.edges:
        if (e.source.id in keys) and (e.target.id in keys):
            edges_from.append(str(e.source.id)+'_'+uid)
            edges_to.append(str(e.target.id)+'_'+uid)
            edges_orientations.append(e.orientation)
    
    edge_ids=server.Cytoscape.createEdges(networkid, edges_from, edges_to)
    
    #for att in ['orientation']:
    d=dict(zip(edge_ids,edges_orientations))
    server.Cytoscape.addEdgeAttributes("orientation", "INTEGER", d)
    
    seqlengths=[ g.vertices[k].attributes['seqlen'] for k in keys ]
    if addseq:
        for att in ['seq']:
            d=dict( zip(nodes, [ str(g.vertices[k].attributes[att]).upper() for k in keys ] ) )
            server.Cytoscape.addNodeAttributes(att, "STRING", d, True)
        for att in ['seqlen']:
            d=dict( zip(nodes, seqlengths ) )
            server.Cytoscape.addNodeAttributes(att, "INTEGER", d, True)
        for att in ['rcseq']:
            d=dict( zip(nodes, [ str(g.vertices[k].attributes[att]) for k in keys ] ) )
            server.Cytoscape.addNodeAttributes(att, "STRING", d, True)
    
    for att in ['coord_origin','coord_contig','contig_start','contig_end','saoffset','input_origin']:
        d=dict( zip(nodes, [ str( eval('g.vertices[k].'+att) ) for k in keys ] ) )
        server.Cytoscape.addNodeAttributes(att, "STRING", d, True)
    
    for att in ['origin','contig_origin']:
        d=dict( zip(nodes, [ str( sorted(eval('g.vertices[k].'+att)) ) for k in keys ] ) )
        server.Cytoscape.addNodeAttributes(att, "STRING", d, True)
    
    d=dict( zip(nodes, [ len(eval('g.vertices[k].origin')) for k in keys ] ) )
    server.Cytoscape.addNodeAttributes('nroforigins', "INTEGER", d, True)
    
    color_mapping=dict()
    for key in keys:
        mapname=str(sorted(g.vertices[key].origin))
        if color_mapping.has_key(mapname):
            color_mapping[mapname].append(key)
        else:
            color_mapping[mapname]=[key]
    
    colors={}
    step=360/float(len(color_mapping))
    i=0
    for mapname in color_mapping.keys():
        hue = i/360.
        lightness = (50 + random.random() * 10)/100.
        saturation = (90 + random.random() * 10)/100.
        colors[mapname]=colorsys.hls_to_rgb(hue, lightness, saturation)
        i+=step
    
    for mapname in color_mapping.keys():
        color=colors[mapname]
        group=color_mapping[mapname]
        server.Cytoscape.setNodeFillColor(networkid, [str(x)+"_"+uid for x in group], int(color[0]*255), int(color[1]*255), int(color[2]*255))
    
    server.Cytoscape.createContinuousMapper('default','seqlen','Node Size', [1., 10., 500.], [10.,10.,50.,100.,200.])
    #server.Cytoscape.getLayoutNames()
    #server.Cytoscape.performLayout(networkid,'hierarchical')

def graph_aln(i, coordsystem=None, kseed=10000, pcutoff=1e-5, clipping=True, 
              minfrac=None, gapopen=-2, gapextend=-1):
    global totalignedseq
    
    g=i.graph
    stack=[i]
    run=0
    orgn=0
    
    for v in g.vertices.values():
        orgn+=v.contig_end-v.contig_start
    
    #check_index(i)
    tips=set()
    skip=set()
    
    while len(stack)>0:
        index=stack.pop(0)
        i=index
        if index==None: #received execute signal, break out of the loop
            break
        
        logging.debug("Extracting all MUMs larger than %s to seed the alignment", kseed)
        matches=index.get_mums(kseed)
        
        #check_colinearity(matches)
        
        if matches==None:
            logging.debug("No more MUMs larger than %s in global index, proceeding with single next largest MUM",kseed)
            stack.append(index)
            break
        
        if len(matches)>1:
            matches=sorted(matches,key=lambda match: match[2],reverse=True) #sort by length
        
        alignedvertices=set()
        verticestoextract=set()
        mergedvertices=set()
        tmpvertices=set()
        
        for match in matches:
            aFrom,bFrom,matchlength,aV,bV,rcmatch=match
            aTo=aFrom+matchlength
            bTo=bFrom+matchlength
            
            assert(aFrom>=0)
            assert(bFrom>=0)
            
            if (aV in alignedvertices) or (bV in alignedvertices):
                continue
            
            pvalue=calc_pvalue(matchlength,(aV.contig_end-aV.contig_start),
                               (bV.contig_end-bV.contig_start), p=0.25,
                                gapopen=gapopen, gapextend=gapextend)
            
            if pvalue>pcutoff:
                logging.info('\033[91mUnaligned Global MUM (p-value=%s, p-cutoff=%s) of length: %d in segment %s.\033[0m', pvalue, pcutoff, matchlength, [(aV.contig_end-aV.contig_start),(bV.contig_end-bV.contig_start)])
                continue
            
            logging.debug('\033[94mAligning Global MUM a[%s:%s], b[%s:%s] (p=%s) of length: %d in segment %s.\033[0m', aFrom, aTo, bFrom, bTo, pvalue, matchlength, [(aV.contig_end-aV.contig_start),(bV.contig_end-bV.contig_start)])
            
            #make sure that aV contains the coordinate system (if it's there)
            if (coordsystem!=None and coordsystem in bV.origin):
                tmpV=aV
                tmpFrom=aFrom
                tmpTo=aTo
                aV=bV
                aFrom=bFrom
                aTo=bTo
                bV=tmpV
                bFrom=tmpFrom
                bTo=tmpTo
            
            if (index.rcindex==1):
                if (bFrom>=bV.rcsaoffset and bV.saoffset!=bV.rcsaoffset):
                    logging.debug("Found match on the reverse complement of node %s, because %s is larger (or equal) than %s",bV.id,bFrom,bV.rcsaoffset)
                    bRCFrom=bFrom
                    l=bV.contig_end-bV.contig_start
                    bFrom=bV.saoffset+(l-matchlength-(bFrom-bV.rcsaoffset))
                    bTo=bFrom+matchlength
                    logging.debug("Adjusted match to the input sequence (saoffset=%s, rcsaoffset=%s, matchlength=%s) in T at %s", bV.saoffset, bV.rcsaoffset, matchlength, bFrom)
                else:
                    l=bV.contig_end-bV.contig_start
                    bRCFrom=bV.rcsaoffset+((bV.saoffset+l)-bTo)
            
            assert(aFrom>=0)
            assert(bFrom>=0)
            
            mums.append(match)
            totalignedseq+=(match[2]*2)
            l1nv, r1nv, l2nv, r2nv, mergedv, tmpv = align(g, aV, aFrom, aTo, bV, bFrom, bTo, rcmatch, None, coordsystem=coordsystem)
            
            if (index.rcindex==1):
                assert(tmpv.indexed==2)
            
            alignedvertices.add(aV)
            alignedvertices.add(bV)

            logging.debug('Aligned Global MUM (%s) of length: %d between nodes: %d[%s:%s] and %d [%s:%s] --> %d',str(rcmatch), matchlength, aV.id, str(aFrom), str(aTo), bV.id, str(bFrom), str(bTo), mergedv.id)
            
            if l1nv!=None:
                logging.log(1,'Relabeling l1nv saoffset=%s contig_start=%s contig_end=%s',l1nv.saoffset, l1nv.contig_start, l1nv.contig_end)
                index.relabel(l1nv)
            if r1nv!=None:
                logging.log(1,'Relabeling r1nv saoffset=%s contig_start=%s contig_end=%s',r1nv.saoffset, r1nv.contig_start, r1nv.contig_end)
                index.relabel(r1nv)
            if l2nv!=None:
                logging.log(1,'Relabeling l2nv saoffset=%s contig_start=%s contig_end=%s',l2nv.saoffset, l2nv.contig_start, l2nv.contig_end)
                index.relabel(l2nv)
            if r2nv!=None:
                logging.log(1,'Relabeling r2nv saoffset=%s contig_start=%s contig_end=%s',r2nv.saoffset, r2nv.contig_start, r2nv.contig_end)
                index.relabel(r2nv)
            
            logging.debug("Updating index aFrom=%s bFrom=%s matchlength=%s vertexid=%s",aFrom, bFrom, matchlength, mergedv.id)
            index.update(aFrom, matchlength, mergedv)
            index.update(bFrom, matchlength, mergedv)
            
            if index.rcindex:
                logging.debug("Updating reverse complement in index From=%s matchlength=%s vertexid=%s", bRCFrom, matchlength, mergedv.id)
                index.update(bRCFrom, matchlength, mergedv)
            
            #get rid of the merged vertices and its suffixes
            verticestoextract.add(mergedv)
            verticestoextract.add(tmpv)
            
            tmpvertices.add(tmpv)
            mergedvertices.add(mergedv)
        
        if len(verticestoextract)==0: #no more valid Global alignments found
            logging.debug("No more valid MUMs in global index of size %s",index.n)
            break
        
        logging.debug("Extracting %s merged vertices %s", len(verticestoextract), [v.id for v in verticestoextract])
        tmp=index.extract(verticestoextract)
        
        del(tmp)
        for v in tmpvertices:
            g.remove_vertex(v)
        
        for mergedv in mergedvertices:
            for direction in [True, False]:
                
                #TODO: this wont work in case of assembly graphs!
                #if needsPrefAln==False and direction==False:
                #    logging.debug("Skipping BFS search in direction False for %s, because no prefix after alignment.",mergedv.id)
                #    continue
                
                #if needsSufAln==False and direction==True:
                #    logging.debug("Skipping BFS search in direction True for %s, because no suffix after alignment.",mergedv.id)
                #    continue
                
                logging.debug("BFS search in direction %d through graph starting at node %s.",direction, mergedv.id)
                
                if (mergedv,direction) in skip:
                    logging.debug("Skip search %s with direction %s, because it was already aligned previously.",mergedv.id, direction)
                    continue
                
                detected_merged_nodes=set()
                bubble=set()
                bubble_vids=list()
                bubble_os=list()
                
                if direction==True:
                    if len(mergedv.edges_from)<2: #have to be at least two paths leaving the merged vertex to be able to detect a bubble
                        continue
                else:
                    if len(mergedv.edges_to)<2: #have to be at least two paths entering the merged vertex to be able to detect a bubble
                        continue
                
                bfsiter=search(g, mergedv, detected_merged_nodes, idirection=direction, condition=isnotmerged)
                for v,o in bfsiter:
                    logging.log(1,"Found vertex: %s",v.id)
                    bubble.add((v,o))
                    bubble_vids.append(v.id)
                    bubble_os.append(o)
                    logging.log(1,"Added vertex: %s",v.id)
                
                logging.debug("BFS search done.")
                
                if len(detected_merged_nodes)==1: # found a bubble
                    sink=detected_merged_nodes.pop()
                    #traverse back
                    d=set()
                    rbubble=set()
                    rbubble_vids=list()
                    rbubble_os=list()
                    
                    #if sink[0].contig_origin!=mergedv.contig_origin: #only align when bubble is enclosed within the same contig
                    #    logging.debug("BFS search found merged vertex %s, but originates from different contig then %s (TURN THIS OFF IN CASE OF ALIGNING ASSEMBLY GRAPHS!)",sink[0].id, mergedv.id)
                    #    logging.debug("Adding %s,%s to tips.", mergedv.id, direction)
                    #    tips.add((mergedv,direction))
                    #    continue
                    
                    if sink[0].origin!=mergedv.origin:
                        logging.debug("Origin between source (%s) and sink (%s) node differs, so can't form a clean bubble", mergedv.origin, sink[0].origin)
                        continue
                    
                    if sink[1]: #same orientation as mergedv
                        logging.debug("BFS search found merged vertex %s, starting reverse search from vertex in direction %d",sink[0].id, not(direction))
                        bfsiter=search(g, sink[0], d, idirection=not(direction), condition=isnotmerged)
                    else:
                        logging.debug("BFS search found merged vertex %s, starting reverse search from vertex in direction %d",sink[0].id, direction)
                        bfsiter=search(g, sink[0], d, idirection=direction, condition=isnotmerged)
                    
                    for v,o in bfsiter:
                        rbubble.add((v,o))
                        rbubble_vids.append(v.id)
                        rbubble_os.append(o)
                    logging.debug("Reverse BFS search done.")
                    
                    if (set(rbubble_vids)!=set(bubble_vids)):
                        logging.debug("Not a clean bubble, because reversed walk finds other nodes: %s, %s",bubble_vids,rbubble_vids)
                        logging.debug("Adding %s,%s to tips.", mergedv.id, direction)
                        tips.add((mergedv,direction))
                        continue
                    
                    if len(d)==1:
                        rsink=d.pop()
                        if rsink[0]!=mergedv:
                            logging.debug("Not a clean bubble, because reversed walk doesn't find: %s, but: %s",mergedv.id, rsink[0].id)
                            logging.debug("Adding %s,%s to tips.", mergedv.id, direction)
                            tips.add((mergedv,direction))
                            continue
                    else:
                        logging.debug("Not a clean bubble, because reversed walk finds multiple merged nodes: %s",[v.id for v,o in d])
                        logging.debug("Adding %s,%s to tips.", mergedv.id, direction)
                        tips.add((mergedv,direction))
                        continue
                    
                    assert(rsink[1]==sink[1])
                    
                    for rvid,ro in zip(rbubble_vids,rbubble_os):
                        if rvid not in bubble_vids:
                            if sink[1]: #do source and sink hav same orientation
                                bubble.add((g.vertices[rvid],ro))
                            else:
                                bubble.add((g.vertices[rvid],not(ro)))
                    
                    logging.debug("\033[115mFound clean bubble consisting of nodes %s, extracting corresponding vertices from index.", [v.id for v,o in bubble])
                    
                    if index.rcindex: #first remove both rc and suffixes from the index
                        tmpbubble=set()
                        for v in bubble:
                            tmpbubble.add(v[0])
                        
                        bitmp=index.extract(tmpbubble)
                        
                        #make sure that within the local alignment the orientation of the nodes is fixed
                        for v,o in bubble:
                            if v.input_origin==0: #we only index reverse complements for input origin 1
                                assert(v.indexed==0)
                            if o==False and v.input_origin==1: #instruction to extract function to take the reverse complement of vertex v
                                v.indexed=1
                            else: #instruction to extract function to take the primary orientation of vertex v
                                v.indexed=0
                            
                            cnt=0
                            for v2,o2 in bubble:
                                if v2.id==v.id:
                                    cnt+=1
                            
                            assert(cnt<=2 and cnt>=1)
                            
                            if cnt==2: #vertex occurs in both orientations...
                                logging.info("Vertex %s occurs in both orientations in clean bubble!",v.id)
                                if v.input_origin==1:
                                    v.indexed=2
                        
                        bi=bitmp.extract(set([v for v,o in bubble]))
                        del(bitmp)
                    else:
                        bi=index.extract(set([v for v,o in bubble]))
                    
                    bubble=set([v for v,o in bubble])
                    
                    logging.debug("Extracting vertices done.")
                    
                    source=(mergedv,direction)
                    bi.graph=g
                    if sink[1] and direction==True: #if the orientation is the same
                        bubble_aln(bi,source,(sink[0],not(direction)),bubble,
                                   coordsystem=coordsystem,pcutoff=pcutoff, 
                                   minfrac=minfrac, gapopen=gapopen, 
                                   gapextend=gapextend)
                        skip.add((sink[0],not(direction)))
                        logging.debug("Discarding %s,%s from set of tips",sink[0].id,not(direction))
                        logging.debug("Discarding %s,%s from set of tips",mergedv.id,direction)
                        tips.discard((sink[0],not(direction)))
                        tips.discard((mergedv,direction))
                    elif sink[1] and direction==False:
                        bubble_aln(bi,(sink[0],not(direction)),source,bubble,
                                   coordsystem=coordsystem,pcutoff=pcutoff, 
                                   minfrac=minfrac, gapopen=gapopen, 
                                   gapextend=gapextend)
                        skip.add((sink[0],not(direction)))
                        logging.debug("Discarding %s,%s from set of tips",sink[0].id,not(direction))
                        logging.debug("Discarding %s,%s from set of tips",mergedv.id,direction)
                        tips.discard((sink[0],not(direction)))
                        tips.discard((mergedv,direction))
                    elif not(sink[1]) and direction==True: #if opposite orientation
                        bubble_aln(bi,source,(sink[0],direction),bubble,
                                   coordsystem=coordsystem,pcutoff=pcutoff, 
                                   minfrac=minfrac, gapopen=gapopen, 
                                   gapextend=gapextend)
                        skip.add((sink[0],direction))
                        logging.debug("Discarding %s,%s from set of tips",sink[0].id,direction)
                        logging.debug("Discarding %s,%s from set of tips",mergedv.id,direction)
                        tips.discard((sink[0],direction))
                        tips.discard((mergedv,direction))
                    else: #if not(sink[1]) and direction==False:
                        bubble_aln(bi,(sink[0],direction),source,bubble,
                                   coordsystem=coordsystem,pcutoff=pcutoff, 
                                   minfrac=minfrac, gapopen=gapopen, 
                                   gapextend=gapextend)
                        skip.add((sink[0],direction))
                        logging.debug("Discarding %s,%s from set of tips",sink[0].id,direction)
                        logging.debug("Discarding %s,%s from set of tips",mergedv.id,direction)
                        tips.discard((sink[0],direction))
                        tips.discard((mergedv,direction))                
                elif len(detected_merged_nodes)==0:
                    logging.debug("Not a single merged node detected in this direction, so consider %s as a tip.", mergedv.id)
                    logging.debug("Adding %s,%s to tips.", mergedv.id, direction)
                    tips.add((mergedv,direction))
                else:
                    logging.debug("Multiple merged nodes (%s) encountered, no clean bubble or tip.", [v.id for v,o in detected_merged_nodes])
        
        logging.info("\033[95m{:.2%} sequence aligned\033[0m".format((totalignedseq/float(orgn))))
        #bubble and merged node are out of the index, go again!
        stack.append(index)
        run+=1
    
    for v,o in tips:
        logging.debug("Tip: %s, %s", v.id, o)
    
    skip=set()
    logging.info("Continue aligning %s tips.", len(tips))
    for tip in tips:
        logging.info("Aligning tip starting at %s,%s",tip[0].id,tip[1])
        
        if tip in skip:
            logging.debug("Skip %s, because it was already tip-aligned.",tip[0].id)
            continue
        
        bubble=set()
        detected_merged_nodes=set()
        bfsiter=search(g, tip[0], detected_merged_nodes, idirection=tip[1], condition=isnotmerged)
        for v,o in bfsiter:
            logging.log(1,"Found vertex: %s",v.id)
            bubble.add((v,o))
            logging.log(1,"Added vertex: %s",v.id)
        
        #reverse search
        if len(detected_merged_nodes)==1:
            sink,sameorientation=detected_merged_nodes.pop()
            bfsiter=search(g, sink, detected_merged_nodes, idirection=not(tip[1]) if sameorientation else tip[1], condition=isnotmerged)
            for v,o in bfsiter:
                bubble.add((v,o))
            
            if len(detected_merged_nodes)>1:
                logging.debug("Unable to align tip, because multiple merged nodes were found when reverse searching from %s.",sink.id)
                continue
            
            logging.debug("Found sink node at %s,%s.",sink.id, not(tip[1]) if sameorientation else tip[1])
            sink=(sink, not(tip[1]) if sameorientation else tip[1])
            
            if i.rcindex: #first remove both rc and suffixes from the index
                tmpbubble=set()
                for v in bubble:
                    tmpbubble.add(v[0])
                
                logging.debug("Extracting vertices %s from index.",[v.id for v in tmpbubble])
                bitmp=i.extract(tmpbubble)
                #make sure that within the local alignment the orientation of the nodes is fixed
                for v,o in bubble:
                    if v.input_origin==0: #we only index reverse complements for input origin 1
                        assert(v.indexed==0)
                    if o==False and v.input_origin==1: #instruction to extract function to take the reverse complement of vertex v
                        v.indexed=1
                    else: #take the primary orientation of the sequence defined on vertex v
                        v.indexed=0
                    cnt=0
                    for v2,o2 in bubble:
                        if v2.id==v.id:
                            cnt+=1
                    assert(cnt<=2 and cnt >=1)
                    if cnt==2: #vertex occurs in both orientations...
                        logging.info("Vertex %s occurs in both orientations in clean bubble!",v.id)
                        v.indexed=2
                bi=bitmp.extract(set([v for v,o in bubble]))
                del(bitmp)
            else:
                bi=i.extract([v for v,o in bubble])
            
            bi.graph=g
            bubble_aln(bi, tip, sink, set([v for v,o in bubble]), 
                       coordsystem=coordsystem, pcutoff=pcutoff, 
                       minfrac=minfrac, tip=True, gapopen=gapopen, 
                       gapextend=gapextend)
                       
            logging.debug("Adding %s,%s to skip set.",sink[0].id, not(tip[1]) if sameorientation else tip[1])
            skip.add((sink[0], not(tip[1]) if sameorientation else tip[1]))
        elif len(detected_merged_nodes)>1:
            logging.debug("Detected junction at tip node: %s, because search found %s. Skipping alignment...",tip[0],[v.id for v,o in detected_merged_nodes])
            continue
        else:
            if i.rcindex: #first remove both rc and suffixes from the index
                tmpbubble=set()
                for v in bubble:
                    tmpbubble.add(v[0])

                bitmp=i.extract(tmpbubble)
                
                #make sure that within the local alignment the orientation of the nodes is fixed
                for v,o in bubble:
                    if v.input_origin==0: #we only index reverse complements for input origin 1
                        assert(v.indexed==0)
                    if o==False and v.input_origin==1: #instruction to extract function to take the reverse complement of vertex v
                        v.indexed=1
                    else: #take the primary orientation of the sequence defined on vertex v
                        v.indexed=0
                    cnt=0
                    for v2,o2 in bubble:
                        if v2.id==v.id:
                            cnt+=1
                    assert(cnt<=2 and cnt >=1)
                    if cnt==2: #vertex occurs in both orientations...
                        logging.error("Vertex %s occurs in both orientations in tip!",v.id)
                        if v.input_origin==1:
                            v.indexed=2
                
                #print [(v.id, v.indexed, v.input_origin) for v,o in bubble]
                bi=bitmp.extract(set([v for v,o in bubble]))
                
                del(bitmp)
            else:
                bi=i.extract([v for v,o in bubble])
            
            bi.graph=g
            bubble_aln(bi, tip, None, set([v for v,o in bubble]), 
                       coordsystem=coordsystem, pcutoff=pcutoff, 
                       minfrac=minfrac, tip=True, gapopen=gapopen, 
                       gapextend=gapextend)
    
    cliptot=0
    tottipnodes=0
    for v in g.vertices.values():
        if len(v.edges_from)+len(v.edges_to)==1 and (coordsystem not in v.origin) and (len(v.origin)==1):
            if clipping:
                g.remove_vertex(v)
            cliptot+=v.contig_end-v.contig_start
            tottipnodes+=1
    
    if clipping:
        logging.info("Clipped of %s bases in tip nodes.",cliptot)
    else:
        logging.info("Total of %s bases (%.2f%%) of sequence unaligned in %s tip nodes.",cliptot, (cliptot/float(orgn))*100, tottipnodes)
    
    return g

def calc_gamma(m, n, l, gleading, gtrailing, gapopen=-2, gapextend=-1, 
               left_aligned=False, right_aligned=False):
    if n>m: #make sure n is smaller than or equal to m
        m,n=n,m
        gleading,gtrailing=gtrailing,gleading
    
    gamma=0
    d=0
    
    #for every diagonal in the matrix
    for i in range(1,n):
        igl=(i*gapextend)+gapopen
        igt=(i*gapextend)+((m-n)*gapextend)+gapopen
        
        if left_aligned and right_aligned:
            g=igt+igl
        elif left_aligned or right_aligned:
            g=igl
        else:
            g=0
        
        dl=n-i
        p=dl-max([1,l-(g-(gleading+gtrailing))])+1
        if p>0:
            gamma+=p
        else:
            break
        
    for i in range(m):
        if i==0 and m==n:
            igl=0
            igt=0
        elif i==0:
            igl=0
            igt=(m-n)*gapextend+gapopen
        else:
            igl=(i*gapextend)+gapopen
            igt=(abs(m-n-i)*gapextend)
            if i!=m-n:
                igt+=gapopen
        
        if left_aligned and right_aligned:
            g=igt+igl
        elif left_aligned or right_aligned:
            g=igl
        else:
            g=0
        
        dl=min([n,m-i])
        p=dl-max([1,l-(g-(gleading+gtrailing))])+1
        if p>0:
            gamma+=p
        else:
            break
    
    return gamma


def calc_pvalue(l,m,n,p=0.25, leading_gap=None, trailing_gap=None, gapopen=-2, 
                gapextend=-1, left_aligned=False, right_aligned=False):
    #TODO: we can observe the value for mu by sampling, then derive beta: beta = (math.log(m*n)/mu)
    #should give better estimation of beta, since the probability of observing a matching bp is not always 0.25, sometimes bigger due to repetitiveness of the genome
    if left_aligned or right_aligned:
        
        gamma=calc_gamma(m,n,l,leading_gap,
                             trailing_gap,
                             gapopen=gapopen,
                             gapextend=gapextend,
                             left_aligned=left_aligned, 
                             right_aligned=right_aligned)
        
        logging.debug('\033[91mGamma is %s (m=%s, n=%s, l=%s, lg=%s, tg=%s, left_aligned=%s, right_aligned=%s, gapopen=%s, gapextend=%s)',
                    gamma,m,n,l,leading_gap,trailing_gap,left_aligned,
                    right_aligned,gapopen,gapextend)
        
    else:
        gamma=(m-l+1)*(n-l+1)
    
    beta=(math.log(1/0.25)) #probability of observing a match by random change defaults to 0.25!
    mu=math.log(gamma)/beta #then on average the max length observed by random change between two sequences of length m and m is mu
    
    gumbel_cdf=math.exp(-math.exp(-(l-mu)/beta)) #probability of observing a match of length 'matchlength' or smaller
    
    limit=math.exp(-math.exp(-((min([m,n])+1)-mu)/beta)) #limitp is now the probability of observing a match smaller than min([m,n]), so should be 1...
    p=1-(gumbel_cdf/limit)
    assert(p<=1)
    assert(p<=1-gumbel_cdf)
    return p

def printSA(i,start=0,n=100,width=None):
    sa=i.SA[start:start+n]
    lcp=i.LCP[start:start+n]
    sep=i.sep
    if width==None:
        width=max(lcp)+1
    t=i.T
    for i,s in enumerate(sa):
        line="{: >10} {} {} {}".format(s,t[s:s+width],lcp[i],s>sep)
        logging.debug(line)

def bubble_aln(i, source, sink, vertices, coordsystem=None, pcutoff=1e-5, 
               tip=False, minfrac=None, gapopen=-2, gapextend=-1):
    global totalignedseq
    
    g=i.graph
    if source!=None and sink!=None:
        logging.debug('Aligning bubble between: %s (%s) and %s (%s) containing vertices %s.', source[0].id, source[1], sink[0].id, sink[1], sorted([v.id for v in vertices]))
    elif source!=None:
        logging.debug('Aligning bubble from source %s (%s) only, containing vertices %s.', source[0].id, source[1], sorted([v.id for v in vertices]))
    else:
        logging.debug('Aligning bubble from sink %s (%s) only, containing vertices %s.', sink[0].id, sink[1], sorted([v.id for v in vertices]))
    
    stack=[(i,source,sink,vertices,tip)]
    run=0;
    while len(stack)>0:
        index,source,sink,vertices,tip=stack.pop(0)
        logging.debug("Extracting local MUM from index of size %s between vertices: %s",index.n,sorted([v.id for v in vertices]))
        
        #TODO: in case of bubble spanning GAP, consider not using gap penalties since they are often way off!
        
        left_aligned=False
        right_aligned=False
        if len(vertices)==2 and (source!=None or sink!=None): #can only calculate penalties when aligning two sequences and either the source or sink is aligned
            if source!=None and sink!=None:
                logging.debug("Extracting scored MUM from bubble")
                left_aligned=True
                right_aligned=True
                match=index.get_scored_mum(left_aligned=True, right_aligned=True,
                                           gap_open=gapopen, gap_extend=gapextend) #returns best scoring MUM, considering GAP penalties for both prefix and suffix
            else: #determine whether the two sequences are left or right aligned!
                if source!=None:
                    if (source[0].saoffset<min([v.saoffset for v in vertices])):
                        left_aligned=True
                        right_aligned=False
                    else: 
                        left_aligned=False
                        right_aligned=True
                else: #only sink is there
                    if (sink[0].saoffset<min([v.saoffset for v in vertices])):
                        left_aligned=True
                        right_aligned=False
                    else: 
                        left_aligned=False
                        right_aligned=True
                assert(tip==True)
                logging.debug("Extracting scored MUM from tip (left_aligned=%s, right_aligned=%s)",left_aligned, right_aligned)
                match=index.get_scored_mum(left_aligned=left_aligned, right_aligned=right_aligned,
                                           gap_open=gapopen, gap_extend=gapextend)
        else:
            logging.debug("Extracting un-penalized MUM, because there are more than two nodes in the bubble or no source and sink!")
            match=index.get_mum()
        
        if match==None:
            logging.debug("No more MUM in index.")
            if index.n==678:
                printSA(index,start=0,n=index.n,width=100)
            continue
        
        if len(match)==6:
            aFrom,bFrom,matchlength,aV,bV,rcmatch=match #returns match with indices within suffix array
            score,leadinggap,trailinggap=None,None,None
        else:
            aFrom,bFrom,matchlength,aV,bV,rcmatch,score,leadinggap,trailinggap=match #returns match with indices within suffix array
        
        aTo=aFrom+matchlength
        bTo=bFrom+matchlength
        
        logging.debug("Found MUM of size %s",matchlength)
        
        if (coordsystem!=None and coordsystem in bV.origin):
            tmpV=aV
            tmpFrom=aFrom
            tmpTo=aTo
            aV=bV
            aFrom=bFrom
            aTo=bTo
            bV=tmpV
            bFrom=tmpFrom
            bTo=tmpTo
        
        
        if minfrac!=None and matchlength/float(min([(aV.contig_end-aV.contig_start),(bV.contig_end-bV.contig_start)]))>=minfrac:
            pvalue=0
        elif matchlength==aV.contig_end-aV.contig_start==bV.contig_end-bV.contig_start: #a match between two nodes of the same size, skip p-value check... TODO: check if they have same neighbors!
            pvalue=0
        else:
            pvalue=calc_pvalue(matchlength,(aV.contig_end-aV.contig_start),
                                (bV.contig_end-bV.contig_start),p=0.25,
                                leading_gap=leadinggap, trailing_gap=trailinggap,
                                gapopen=gapopen, gapextend=gapextend,
                                left_aligned=left_aligned, right_aligned=right_aligned)
        
        if pvalue>pcutoff:# and matchlength<(min([aV.contig_end-aV.contig_start,bV.contig_end-bV.contig_start])/2.):
            logging.info('\033[91mUnaligned %s MUM a[%s:%s], b[%s:%s] (p-value=%s, p-cutoff=%s) of length: %d (score=%s, gap_leading=%s, gap_trailing=%s) in segment %s \033[0m', 'bubble' if not(tip) else 'tip', aFrom, aTo, bFrom, bTo, pvalue, pcutoff, matchlength, score, leadinggap, trailinggap, [(aV.contig_end-aV.contig_start),(bV.contig_end-bV.contig_start)])
            continue
        
        #determine if we matched the reverse complement of the second input sequence,
        #if so, calculate the original coordinates
        if (index.rcindex==1):
            if (bFrom>=bV.rcsaoffset and bV.saoffset!=bV.rcsaoffset):
                bRCFrom=bFrom
                l=bV.contig_end-bV.contig_start
                bFrom=bV.saoffset+(l-matchlength-(bFrom-bV.rcsaoffset))
                bTo=bFrom+matchlength
            else:
                l=bV.contig_end-bV.contig_start
                bRCFrom=bV.rcsaoffset+((bV.saoffset+l)-bTo)
        
        mums.append(match)
        totalignedseq+=(match[2]*2)
        l1nv, r1nv, l2nv, r2nv, mergedv, tmpv = align(g, aV, aFrom, aTo, bV, bFrom, bTo, rcmatch, None)
        
        logging.debug('\033[92mAligned Local MUM (rcmatch=%s) a[%s:%s], b[%s:%s]  (p-value=%s) of length: %d (score=%s, gap_leading=%s, gap_trailing=%s) in segment %s between nodes %s and %s, created node %s\033[0m', rcmatch, aFrom, aTo, bFrom, bTo, pvalue, matchlength, score, leadinggap, trailinggap, [(aV.contig_end-aV.contig_start),(bV.contig_end-bV.contig_start)], aV.id, bV.id, mergedv.id)
        
        if l1nv!=None:
            vertices.add(l1nv)
            logging.log(1,'Relabeling l1nv saoffset=%s contig_start=%s contig_end=%s',l1nv.saoffset, l1nv.contig_start, l1nv.contig_end)
            index.relabel(l1nv)        
        if r1nv!=None:
            vertices.add(r1nv)
            logging.log(1,'Relabeling r1nv saoffset=%s contig_start=%s contig_end=%s',r1nv.saoffset, r1nv.contig_start, r1nv.contig_end)
            index.relabel(r1nv)        
        if l2nv!=None:
            vertices.add(l2nv)
            logging.log(1,'Relabeling l2nv saoffset=%s contig_start=%s contig_end=%s',l2nv.saoffset, l2nv.contig_start, l2nv.contig_end)
            index.relabel(l2nv)
        if r2nv!=None:
            vertices.add(r2nv)
            logging.log(1,'Relabeling r2nv saoffset=%s contig_start=%s contig_end=%s',r2nv.saoffset, r2nv.contig_start, r2nv.contig_end)
            index.relabel(r2nv)
        
        #update the order of the suffixes
        index.update(aFrom, matchlength, mergedv)
        index.update(bFrom, matchlength, mergedv)
        
        if index.rcindex==1:
            index.update(bRCFrom, matchlength, mergedv)
        
        vertices.discard(aV)
        vertices.discard(bV)
        
        #take out the matched suffixes from the index
        s=set([mergedv,tmpv])
        tmp=index.extract(s)
        
        g.remove_vertex(tmpv)
        del(tmp)
        
        if index.n<=1:
            logging.debug("Local index smaller or equal to 1, stop aligning bubble.")
            continue
        
        if source==None and sink==None: #left-overs in the bubble...
            logging.debug("Vertices %s could not further be aligned, because source and sink node are missing.",sorted([v.id for v in vertices]))
            #if len(vertices)>1:
            #    stack.append((index,None,None,vertices))
            continue
        
        if sink!=None:
            sinksearchdirection=sink[1]
            #determine the orientation of the mergedvertex with respect to the source/sink nodes
            s1_=set()
            detected_merged_nodes=set()
            logging.debug("Segmenting bubble by searching in direction %s from node %s",sink[1],sink[0].id)
            bfsiter=search(g, sink[0], detected_merged_nodes, idirection=sink[1], condition=isnotmerged)
            for v,o in bfsiter:
                s1_.add(v)
            if (mergedv,False) in detected_merged_nodes: #TODO: can it happen that we find it on both orientations!?
                logging.debug("Found merged vertex with opposite orientation wrt target, flip search direction from %s to %s",sinksearchdirection,not(sinksearchdirection))
                sinksearchdirection=not(sinksearchdirection)
            logging.debug("Found merged node(s) %s with relative orientation(s) %s expect to find node %s",[v.id for v,o in detected_merged_nodes],[o for v,o in detected_merged_nodes],mergedv.id)
            s1_ = vertices & s1_
            
            #segment graph --> search both directions starting at mergedv
            s1=set()
            detected_merged_nodes=set()
            logging.debug("Segmenting bubble by searching in direction %s from node %s",not(sinksearchdirection),mergedv.id)
            bfsiter=search(g, mergedv, detected_merged_nodes, idirection=not(sinksearchdirection), condition=isnotmerged)
            for v,o in bfsiter:
                s1.add(v)
            logging.debug("Found merged node(s) %s with relative orientation(s) %s expect to find node %s",[v.id for v,o in detected_merged_nodes],[o for v,o in detected_merged_nodes],sink[0].id)
            s1 = vertices & s1
            is1= s1 & s1_

        if source!=None:
            logging.debug("Source is %s, search direction is %s",source[0].id,source[1])
            sourcesearchdirection=source[1]
            s2_=set()
            detected_merged_nodes=set()
            logging.debug("Segmenting bubble by searching in direction %s from node %s",source[1],source[0].id)
            bfsiter=search(g, source[0], detected_merged_nodes, idirection=source[1], condition=isnotmerged)
            for v,o in bfsiter:
                s2_.add(v)
            if (mergedv,False) in detected_merged_nodes: #TODO: can it happen that we find it on both orientations!?
                logging.debug("Found merged vertex with opposite orientation wrt source, flip search direction from %s to %s",sourcesearchdirection,not(sourcesearchdirection))
                sourcesearchdirection=not(sourcesearchdirection)
            logging.debug("Found merged node(s) %s with relative orientation(s) %s expect to find node %s",[v.id for v,o in detected_merged_nodes],[o for v,o in detected_merged_nodes],mergedv.id)
            s2_ = vertices & s2_
            
            s2=set()
            detected_merged_nodes=set()
            logging.debug("Segmenting bubble by searching in direction %s from node %s",not(sourcesearchdirection),mergedv.id)
            bfsiter=search(g, mergedv, detected_merged_nodes, idirection=not(sourcesearchdirection), condition=isnotmerged)
            for v,o in bfsiter:
                s2.add(v)
            logging.debug("Found merged node(s) %s with relative orientation(s) %s expect to find node %s",[v.id for v,o in detected_merged_nodes],[o for v,o in detected_merged_nodes],source[0].id)
            s2 = vertices & s2
            is2=s2 & s2_ #vertices on the same path
        
        if source!=None and sink!=None:
            logging.debug("Sink is %s, search direction is %s",sink[0].id,sink[1])
            if is1.intersection(is2)!=set(): #overlap in the segmentation, because merged node was not on the 'critical path'
                logging.debug("Could not segment bubble, continue with next MUM.")
                stack.append((index,source,sink,vertices,False))
                continue
            else: #a path through the bubble can be segmented
                #now check if it segments the entire bubble
                s1= s1 | s1_
                s2= s2 | s2_
                if s1.intersection(s2)!=set():
                    #it doesn't, so only segment the path
                    s1=is1
                    s2=is2
                logging.debug("Segmenting bubble of vertices %s, into %s and %s.",sorted([v.id for v in vertices]),sorted([v.id for v in s1]),sorted([v.id for v in s2]))
        
        if sink!=None:
            vertices = vertices - s1
            if s1!=set():
                i1=index.extract(s1)
                logging.debug("Adding new bubble of vertices %s to stack.",sorted([v.id for v in s1]))
                stack.append((i1,(mergedv,not(sinksearchdirection)),sink,s1,False))
        
        if source!=None:
            vertices = vertices - s2
            if s2!=set():
                i2=index.extract(s2)
                logging.debug("Adding new bubble of vertices %s to stack.",sorted([v.id for v in s2]))
                stack.append((i2,source,(mergedv,not(sourcesearchdirection)),s2,False))
        
        if vertices==set():
            logging.debug("No more vertices to segment.")
            continue
        
        if source==None:
            logging.debug("Adding vertices %s to stack for tip alignment (sink).",sorted([v.id for v in vertices]))
            stack.append((index,None,(mergedv,sinksearchdirection),vertices,True))
        
        if sink==None:
            logging.debug("Adding vertices %s to stack for tip alignment (source).",sorted([v.id for v in vertices]))
            stack.append((index,(mergedv,sourcesearchdirection),None,vertices,True))
        
        if len(vertices)>1: #anything left-over in the index?
            logging.debug("Vertices %s left over in bubble after alignment, could not be segmented...")
            #stack.append((index,None,None,vertices)) #TODO: consider if this is wise!! Does not guarantee that alignment stays co-linear
        
        run+=1
    
    return g

def isnotmerged(s,v):
    return not(v.input_origin==2) #saoffset field is -1 when vertex gets merged..

processes=[]

def check_index(index):
    import numpy as np
    SAi=index.SAi
    SA=index.SA
    TIiv=index.TIiv
    T=index.T
    if index.n>1:
        lcp=index.LCP
        for i,l in enumerate(lcp):
            if i==0:
                assert(l==0)
                continue
            
            if T[SA[i]:SA[i]+l]!=T[SA[i-1]:SA[i-1]+l]:
                print 'INVALID SA/LCP --> LCP too large', index.n, index.LCP, i
                printSA(index,start=i-10)
                assert(True==False)
                
            if T[SA[i]+l]==T[SA[i-1]+l] and (T[SA[i]+l]!='$' and T[SA[i-1]+l]!='$'):
                print 'INVALID SA/LCP --> LCP too small', index.n, index.LCP, i
                printSA(index,start=i-1,n=3)
                assert(True==False)
    
    if len(np.unique(SA))!=len(SA):
        print 'SA',SA
        print 'uSA',np.unique(SA)
    
    assert(len(np.unique(SA))==len(SA))
    
    tmp=[]
    
    for s in SA:
        if TIiv[s]==None:
            print s
        assert(TIiv[s]!=None)
        tmp.append(TIiv[s].id)
        assert(s<len(SAi))

    sSA=sorted(SA)
    dFrom=[]
    dTo=[]
    for i,s in enumerate(sSA):
        if i==0:
            dFrom.append(s)
            continue
        if i==len(sSA)-1:
            dTo.append(s)
            continue
        if s+1!=sSA[i+1]:
            dTo.append(s)
            continue
        if s!=sSA[i-1]+1:
            dFrom.append(s)
            continue
        
    for i,c in enumerate(SA):
        if (SAi[c]!=i):
            print c,i,SAi[c]
        
        assert(SAi[c]==i)

def revcomp(s):
    """Return the complementary sequence string."""
    basecomplement = {'A':'T','C':'G','G':'C','T':'A','N':'N','a':'t','c':'g',\
                        'g':'c','t':'a','n':'n','Y':'R','R':'Y','K':'M','M':'K',\
                        'S':'S','W':'W','B':'V','V':'B','D':'H','H':'D','N':'N',\
                        'X':'X','-':'-'}                        
    letters = list(s)
    letters = [basecomplement[base] for base in letters]
    letters.reverse()
    return ''.join(letters)

#TODO: save graphs by specification of Heng Li's gfa -> http://lh3.github.io/2014/07/23/first-update-on-gfa/
def save(g, outputfile, index, compress=True):
    T=index.T
    if compress:
        filename=outputfile+'.gfasta.gz'
        f=gzip.open(filename,'wb')
    else:
        filename=outputfile+'.gfasta'
        f=open(filename,'w')
    sep="|"
    sep2=";"
    sep3=","
    #TODO: very nasty solution, but write a dummy node that indicates which samples are available in the graph
    f.write('>')
    for i,o in enumerate(g.origins):
        f.write(str(i)+sep2+o.replace(sep,'').replace(sep2,'').replace(sep3,'')+sep)
    f.write('\nN\n')
    for v in g.vertices.values():
        f.write('>'+str(v.id)+sep)
        for e in v.edges_to:
            if e.orientation==0:
                f.write(str(e.source.id)+sep3+str(e.orientation)+sep2)
            else: #2
                assert(e.orientation==2)
                if (e.source==v):
                    f.write(str(e.target.id)+sep3+str(e.orientation)+sep2)
                else:
                    f.write(str(e.source.id)+sep3+str(e.orientation)+sep2)
        f.write(sep)
        for e in v.edges_from:
            if e.orientation==0:
                f.write(str(e.target.id)+sep3+str(e.orientation)+sep2)
            else: #2
                assert(e.orientation==1)
                if (e.source==v):
                    f.write(str(e.target.id)+sep3+str(e.orientation)+sep2)
                else:
                    f.write(str(e.source.id)+sep3+str(e.orientation)+sep2)
        f.write(sep+sep2.join([str(o).replace(sep,'').replace(sep2,'').replace(sep3,'') for o in v.origin])+sep) #file origin
        f.write(sep2.join([str(o).replace(sep,'').replace(sep2,'').replace(sep3,'') for o in v.contig_origin])+sep) #contig origin
        f.write(v.coord_origin.replace(sep,'').replace(sep2,'').replace(sep3,'')+sep) #coordinate file origin
        f.write(v.coord_contig.replace(sep,'').replace(sep2,'').replace(sep3,'')+sep) #coordinate contig origin
        f.write(str(v.contig_start)+sep)
        f.write(str(v.contig_end)+'\n')
        f.write(T[v.saoffset:v.saoffset+(v.contig_end-v.contig_start)]+'\n')
    f.close()
    return filename

def plot_variant_by_position(g,T,pos,degree=10):
    for v in g.vertices.values():
        if v.contig_end==pos-1: #variants are one-based
            plot_neighborhood(g,T,v,degree=degree)
            return

def plot_neighborhood(g,T,v,degree=10):
    keys=[v.id]
    bfs_right=search(g,v,[])
    bfs_left=search(g,v,[],idirection=False)
    for i in range(degree):
        try:
            keys.append(bfs_right.next()[0].id)
            keys.append(bfs_left.next()[0].id)
        except:
            pass
    for k in keys:
        v=g.vertices[k]
        l=v.contig_end-v.contig_start
        g.vertices[v.id].attributes['seq']=T[v.saoffset:v.saoffset+l]
        if g.vertices[v.id].input_origin==1 and rindex.rcindex==1:
            g.vertices[v.id].attributes['rcseq']=T[v.rcsaoffset:v.rcsaoffset+l] #revcomp(T[v.saoffset:v.saoffset+l])
        else:
            g.vertices[v.id].attributes['rcseq']=""
        g.vertices[v.id].attributes['seqlen']=l
    plot(g, keys=keys, addseq=True)

def plot_bubble(g,T,v,degree=10, direction=True):
    keys=[v.id]
    bfsb=search(g,v,[],idirection=direction,maxdegree=degree)
    for v in bfsb:
        keys.append(v[0].id)
    
    for k in keys:
        v=g.vertices[k]
        l=v.contig_end-v.contig_start
        g.vertices[v.id].attributes['seq']=T[v.saoffset:v.saoffset+l]
        if g.vertices[v.id].input_origin==1 and rindex.rcindex==1:
            g.vertices[v.id].attributes['rcseq']=T[v.rcsaoffset:v.rcsaoffset+l] #revcomp(T[v.saoffset:v.saoffset+l])
        else:
            g.vertices[v.id].attributes['rcseq']=""
        g.vertices[v.id].attributes['seqlen']=l
    plot(g, keys=keys, addseq=True)

def plot_gfasta(filename):
    import GSA
    i=GSA.index(filename)
    g=i.graph
    T=i.T
    for v in g.vertices.values():
        l=v.contig_end-v.contig_start
        g.vertices[v.id].attributes['seq']=T[v.saoffset:v.saoffset+l]
        if g.vertices[v.id].input_origin==1 and index.rcindex==1:
            g.vertices[v.id].attributes['rcseq']=T[v.rcsaoffset:v.rcsaoffset+l] #revcomp(T[v.saoffset:v.saoffset+l])
        else:
            g.vertices[v.id].attributes['rcseq']=""
        g.vertices[v.id].attributes['seqlen']=l
    plot(g, keys=g.vertices.keys(), addseq=True)

def plot_graph(index):
    g=index.graph
    T=index.T
    for v in g.vertices.values():
        l=v.contig_end-v.contig_start
        g.vertices[v.id].attributes['seq']=T[v.saoffset:v.saoffset+l]
        if g.vertices[v.id].input_origin==1 and index.rcindex==1:
            g.vertices[v.id].attributes['rcseq']=T[v.rcsaoffset:v.rcsaoffset+l] #revcomp(T[v.saoffset:v.saoffset+l])
        else:
            g.vertices[v.id].attributes['rcseq']=""
        g.vertices[v.id].attributes['seqlen']=l
    plot(g, keys=g.vertices.keys(), addseq=True)


#-l10 -n --noclipping -k1000 -c /Users/jasperlinthorst/Documents/phd/data/CHM1/hg19/chr1.fa.gz /Users/jasperlinthorst/Documents/phd/data/CHM1/hg19/chr1.fa.gz /Users/jasperlinthorst/Documents/phd/data/CHM1/PacBioCHM1_bychromosome/targets_for_chr1.fasta
def main():
    usage = "usage: galn.py [options] <sequence1.(g)fasta> <sequence2.(g)fasta> ..."
    parser = argparse.ArgumentParser(usage)
    parser.add_argument('graphs', nargs='*', help='Fasta or gfasta files specifying either assembly/alignment graphs (.gfasta) or sequences (.fasta). When only one gfasta file is supplied, variants are called within the gfasta file.')
    parser.add_argument("-o", "--output", dest="output", help="Prefix of the variant and alignment graph files to produce, default is \"sequence1_sequence2\"")
    parser.add_argument("-n", "--norc", action="store_false", dest="rcindex", default=True, help="Whether to index reverse complements of nodes/contigs as well.")
    parser.add_argument("-k", dest="kseed", type=int, default=10000, help="MUMs over this size will be aligned throughout the graphs to start up the alignment process (default 10000).")
    parser.add_argument("-p", dest="pcutoff", type=float, default=1e-5, help="If, the probability of observing a MUM of the observed length by random change becomes larger than this cutoff the alignment is stopped.")
    parser.add_argument("--gapopen", dest="gapopen", type=int, default=-5, help="Gap open penalty")
    parser.add_argument("--gapextend", dest="gapextend", type=int, default=-1, help="Gap extension penalty.")
    parser.add_argument("-f", dest="minfrac", type=float, default=None, help="If the fraction of MUM over the sequence aligned in a bubble is larger than this value, skip p-value calculation. Allows skipping p-value calculation for smaller bubbles, smaller bubbles means more variant calls.")
    parser.add_argument("-c", "--coordinate-system", dest="coordsystem", default=None, help="The name (*** so not the actual file) of the input file to be used to keep track of locations of variations, such that they can be annotated later on.")
    parser.add_argument("-l", "--log-level", dest="loglevel", default=20, type=int, help="Log level: 10=debug 20=info (default) 30=warn 40=error 50=fatal")
    parser.add_argument("--clipping", action="store_true", dest="clipping", default=False, help="Whether or not to clip off tip nodes after the alignment")
    parser.add_argument("--64", dest="large", action="store_true", default=False, help="Whether we need a 64 bit integers to store suffix array")
    parser.add_argument("--cytoscape", dest="cytoscaperpcloc", default=None, help="The address and port of a cytoscape-rpc server for plotting the resulting alignment graph (e.g. localhost:9000)")
    parser.add_argument("--vcfmin", dest="minvarsize", default=0, type=int, help="The min size of a variant for it be written to the vcf.")
    parser.add_argument("--vcfmax", dest="maxvarsize", default=None, type=int, help="The max size of a variant for it to be written to the vcf.")
    parser.add_argument("--invmin", dest="minnwsize", default=10, type=int, help="The min inversion detection size, inversions smaller than this threshold will not be reported in the vcf INFO field 'INVERSION'.")
    parser.add_argument("--invmax", dest="maxnwsize", default=1000, type=int, help="The max inversion detection size, inversions larger than this threshold will not be reported in the vcf INFO field 'INVERSION'.")
    
    args = parser.parse_args()
    global g, rindex, mums, totalignedseq, extractedvertices
    
    logging.basicConfig(format='%(asctime)s %(message)s', datefmt='%m/%d/%Y %I:%M:%S %p', level=args.loglevel)
    
    graphname=""
    T=""

    #maybe wildcard specification so try parsing it first
    if len(args.graphs)==1:
        args.graphs = glob.glob(args.graphs[0])
    
    base_filename='_'.join([os.path.basename(graph).replace('.fasta','').replace('.gfasta','').replace('.fa','').replace('.gz','') for graph in args.graphs])
    if args.output==None:
        args.output=base_filename
    
    if args.large:
        logging.info("Using 64-bit index")
        import GSA_64 as GSA
    else:
        import GSA
    
    if len(args.graphs)==1:
        logging.info("Specify at least 2 (g)fasta files for alignment.")
        logging.info("Calling variants in %s...",args.graphs[0])
        rindex=GSA.index(args.graphs[0])
        vcffile,nvars,ngaps,ninversions,nsvs=bubbles(rindex.graph, rindex.T, args.output, minvarsize=args.minvarsize, maxvarsize=args.maxvarsize, maxnwsize=args.maxnwsize, minnwsize=args.minnwsize)
        logging.info("Variants written to %s.",vcffile)
        
        if args.cytoscaperpcloc!=None: #if cytoscape is specified plot the graph
            logging.info("Trying to plot the graph at %s.",args.cytoscaperpcloc)
            plot_graph(rindex)
        return
    
    if len(args.graphs)==0:
        parser.error("No (g)fasta files specified.")
        return
    
    global identity
    identity=[]
    #align specified graphs/sequences
    for i in range(1,len(args.graphs)):
        if (i==1):
            g1=args.graphs[0]
            g2=args.graphs[i]
        else:
            g1=graphname
            g2=args.graphs[i]
        
        if not(os.path.isfile(g1)):
            parser.error("{} is not a file.".format(g1))
        
        if not(os.path.isfile(g2)):
            parser.error("{} is not a file.".format(g2))
        
        logging.debug("Constructing index...")
        rindex=GSA.index(g1, g2, args.rcindex)
        logging.debug("Index construction done.")
        
        mums=[]
        totalignedseq=0
        d=dict()
        d['f1']=g1
        d['f2']=g2
        d['contigs1']=[v.contig_origin.copy().pop() for v in rindex.graph.vertices.values() if v.saoffset<rindex.sep]
        d['contigs2']=[v.contig_origin.copy().pop() for v in rindex.graph.vertices.values() if v.saoffset>rindex.sep]
        d['contigs1_len']=[v.contig_end-v.contig_start for v in rindex.graph.vertices.values() if v.saoffset<rindex.sep]
        d['contigs2_len']=[v.contig_end-v.contig_start for v in rindex.graph.vertices.values() if v.saoffset>rindex.sep]
        
        #extract all mums do clustering on graph with weighted edges, experimental...
        precluster=False
        if precluster:
            for vs in cluster_contigs(rindex):
                logging.info('Aligning contigs %s',[v.id for v in vs])
                logging.info('Extracting contigs %s from index',[v.id for v in vs])
                index=rindex.extract(vs)
                index.graph=rindex.graph
                #for every subgraph, do graph align
                g=graph_aln(index,coordsystem=args.coordsystem,
                                kseed=args.kseed,
                                pcutoff=args.pcutoff, clipping=args.clipping,
                                minfrac=args.minfrac)
        else:
            g=graph_aln(rindex,coordsystem=args.coordsystem,
                                kseed=args.kseed,
                                pcutoff=args.pcutoff, clipping=args.clipping,
                                minfrac=args.minfrac, gapextend=args.gapextend,
                                gapopen=args.gapopen)
        
        #before saving and variant calling, flip orientation of all nodes that have an opposite orientation wrt a merged node (thus can only be nodes with input_origin==1 and when rcindex==True)
        #loop over all nodes with condition True and call flip_orientation!
        if rindex.rcindex==True:
            for v in g.vertices.values(): #for all vertices
                if v.input_origin==1:
                    eos=[e.orientation for e in v.edges_from | v.edges_to]
                    if 1 in eos or 2 in eos: #if one of the edges in the associated edges has reverse orientation
                        logging.debug("Flipping orientation of vertex %s",v.id)
                        v.flip_orientation()
        
        eos=[e.orientation for e in g.edges]
        if(eos.count(0)!=len(eos)):
            logging.error('Not all nodes in the alignment graph have same orientation!')
        
        if i==len(args.graphs)-1: #last graph write to output
            tgraph=args.output #final graph
            tvcf=args.output #final graph
            tmums=args.output
        else:
            tmpname='_'+os.path.basename(g2)
            tgraph=tmpname #tmp graph
            tvcf=tmpname #final graph
            tmums=tmpname
        
        T=rindex.T
        logging.info("Calling variations in graph...")
        vcffile,nvars,ngaps,ninversions,nsvs=bubbles(g, T, tvcf, minvarsize=args.minvarsize, maxvarsize=args.maxvarsize, maxnwsize=args.maxnwsize, minnwsize=args.minnwsize)
        logging.info("Variant calling done and written to %s.",vcffile)
        logging.info("Called %s variants of which %s closed gaps, %s inversions and %s large (>50bp) indels.",nvars,ngaps,ninversions,nsvs)
        graphname=save(g, tgraph, rindex, compress=True)
        logging.info("Graph alignment between %s and %s done and written to %s.", g1, g2, graphname)
        
        logging.info("Pickling mums that were used for the alignment...")
        d['mums']=[(mum[0],mum[1],mum[2],mum[3].contig_origin.copy().pop(),mum[4].contig_origin.copy().pop(),mum[5]) for mum in mums]
        pickle.dump(d, file(tmums+'.pickle', 'w'))
        logging.info("Pickling done, written to %s.",tmums+'.pickle')
        
        input1len=rindex.sep
        input2len=(rindex.orgn-rindex.sep)/2 if rindex.rcindex else rindex.orgn-rindex.sep
        
        logging.info("Identity (tot_aligned_sequence/(len(s1)+len(s2))) between %s and %s is %.2f%%.",os.path.basename(g1), os.path.basename(g2), (totalignedseq/float(input1len+input2len))*100 )
        logging.info("Identity genome/graph %s: ((tot_aligned_sequence/2)/len(s1)) is %.2f%%.",os.path.basename(g1), ((totalignedseq/2)/float(input1len))*100 )
        logging.info("Identity genome/graph %s: ((tot_aligned_sequence/2)/len(s2)) is %.2f%%.",os.path.basename(g2), ((totalignedseq/2)/float(input2len))*100 )
    
        totunalignedbases=0
        totunalignedvertices=0
        commonseq=0
        allseq=0
        
        n=len(g.origins)
        
        d=dict()
        for v in g.vertices.values():
            l=v.contig_end-v.contig_start
            allseq+=l
            
            #if d.has_key(len(v.origin)):
            #    d[len(v.origin)]+=l
            #else:
            #    d[len(v.origin)]=l
            
            if len(v.origin)==n: #node is common to all input genomes
                commonseq+=l
        
        logging.info("%.2f%% of the sequence in the graph is common to all input genomes", (commonseq/float(allseq))*100 )
        identity.append(commonseq/float(allseq))
        
#        if len(v.edges_from)<=1 and len(v.edges_to)<=1 and len(v.origin)==1:
#            if len(v.edges_from)>0 or len(v.edges_to)>0:
#                for eto,efrom in zip(v.edges_from,v.edges_to):
#                    if len(eto.target.edges_to)<=1 and len(efrom.target.edges_from)<=1:
#                        totunalignedbases+=l
#                        totunalignedvertices+=1
#                        logging.info("Vertex %s is not part of the alignment (tip)",v.id)
#            else:
#                totunalignedbases+=l
#                totunalignedvertices+=1
#                logging.info("Vertex %s is not part of the alignment (unconnected)",v.id)
#    
#    logging.info("%s out of %s bases are not part of the alignment.",totunalignedbases,rindex.orgn)
#    logging.info("%s vertices are not part of the alignment.",totunalignedvertices)
    
    if args.cytoscaperpcloc!=None:
        logging.info("Trying to plot the graph at %s.",args.cytoscaperpcloc)
        plot_graph(rindex)
    
    logging.info("All graphs/sequences are aligned.")
        
#-l10 DRR002013.p3.preprocessed.gfasta DRR002014.p3.preprocessed.gfasta
#-l10 /Users/jasperlinthorst/Documents/phd/data/e.coli/de-novo/DRR002013.p3.preprocessed.gfasta /Users/jasperlinthorst/Documents/phd/data/e.coli/de-novo/DRR002014.p3.preprocessed.gfasta
#/Users/jasperlinthorst/Documents/phd/data/HLA/A_gen.000.fasta /Users/jasperlinthorst/Documents/phd/data/HLA/A_gen.001.fasta
#../../data/e.coli/de-novo/DRR002013.p5.fq.gz ../../data/e.coli/de-novo/DRR002014.p5.fq.gz
if __name__ == "__main__":
    main()
