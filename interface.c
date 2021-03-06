#include "Python.h"
#include "reveal.h"
#include <divsufsort.h>
#include <pthread.h>

static PyObject *RevealError;

pthread_mutex_t mutex, python;

RevealIndex **index_queue;
int maxqsize=QUEUE_BUF,qsize=0,qstart=0,aw,nmums,err_flag=0,die=0;

static PyObject *addsample(RevealIndex *self, PyObject *args)
{
    PyObject * sample;
    
    sample=PyTuple_GetItem(args, 0);
    
    if (sample==NULL) {
        PyErr_SetString(RevealError, "Specify name of sample as argument.");
        return NULL;
    }
    
    if (PyString_Check(sample)){
        PyList_Append(self->samples,sample);
    } else {
        PyErr_SetString(RevealError, "Sample name has to be a string.");
        return NULL;
    }

    if (self->nsamples>0){
        self->nsep= (int*) realloc(self->nsep,(self->nsamples)*sizeof(int));
        if (self->nsep==NULL){
            PyErr_SetString(RevealError, "Failed to add sample.");
            return NULL;
        }
        self->nsep[self->nsamples-1]=self->n-1;
        //printf("sample %d at %d\n",self->nsep[self->nsamples-1],self->nsamples-1);
    } 
    
    self->nsamples++;
    Py_INCREF(Py_None);
    return Py_None;
}

static PyObject *addsequence(RevealIndex *self, PyObject *args)
{
    //call add sequence only if self->sep[self->nsamples]
    char * seq;
    int l;
    int s;
    if (!PyArg_ParseTuple(args, "s#", &seq, &l))
        return NULL;
   
    //realloc space for T
    char *tmp=realloc(self->T,(self->n+(l+1)+1)*sizeof(char));
    
    if (tmp!=NULL){
        self->T=tmp;
    } else {
        PyErr_SetString(RevealError, "Realloc for T failed.");
        return NULL;
    }
    
    s=self->n;
    memcpy(self->T+self->n,seq,(l+1)*sizeof(char));
    
    self->T[self->n+l]='$'; //add sentinel
    self->T[self->n+l+1]='\0'; //add sentinel
    
    self->n=self->n+l+1;
    PyObject *intv=Py_BuildValue("(i,i)",s,self->n-1);
    
    PyList_Append(self->nodes,intv);

    return intv;
};

int compute_lcp(char *T, int *SA, int *SAi, int *LCP, int n) {
    int h=0, i, j, k;
    for (i = 0; i < n; i++) {
        k = SAi[i];
        if (k == 0) {
            LCP[k] = -1;
        } else {
            j = SA[k-1];
            while ((i - h < n) && (j + h < n) && (T[i+h] == T[j+h]) && T[i+h]!='$' && T[i+h]!='N' ) { ++h; } //stop comparing when a sentinel or N is encountered, so we dont find matches that span them
            LCP[k] = h;
        }
        if (h > 0) --h;
    }
    return 0;
}

int build_SO(RevealIndex *index){
    int i=0,j=0;
    for (i=0;i<index->nsamples;i++){
        if (i==0){
            for (j=0; j<=index->nsep[i]; j++){
                index->SO[j]=i;
            }
        } else if (i==index->nsamples-1) {
            for (j=index->nsep[i-1]+1; j<index->n; j++){
                index->SO[j]=i;
            }
        } else {
            for (j=index->nsep[i-1]+1; j<=index->nsep[i]; j++){
                index->SO[j]=i;
            }
        }
    }
    return 0;
}

static PyObject *construct(RevealIndex *self, PyObject *args)
{
    if (self->n==0){
        PyErr_SetString(RevealError, "No text to index.");
        return NULL;
    }
    
    self->SA=malloc(sizeof(int)*self->n);
    if (self->SA==NULL){
        PyErr_SetString(RevealError, "Failed to allocate enough memory for SA.");
        return NULL;
    }
    
    if (divsufsort((const sauchar_t *) self->T, self->SA, self->n)!=0){
        PyErr_SetString(RevealError, "divsufsort failed");
        return NULL;
    }
    
    self->SAi = malloc(sizeof(int)*(self->n)); //inverse of SA
    if (self->SAi==NULL){
        PyErr_SetString(RevealError, "Failed to allocate enough memory for SAi.");
        return NULL;
    }
    
    int i;
    for (i=0; i<self->n; i++) {
        self->SAi[self->SA[i]]=i;
    }
    
    self->LCP=malloc(sizeof(int)*self->n);
    if (self->LCP==NULL){
        PyErr_SetString(RevealError, "Failed to allocate enough memory for LCP.");
        return NULL;
    }
    
    compute_lcp(self->T, self->SA, self->SAi, self->LCP, self->n);
    
    if (self->nsamples>2){
         self->SO = malloc(self->n*sizeof(uint16_t));
         if (build_SO(self)!=0){
            PyErr_SetString(RevealError, "Failed to construct SO.");
            return NULL;
         };
    }

    self->main=(PyObject *) self;
    
    Py_INCREF(Py_None);
    return Py_None;
};

static PyObject *align(RevealIndex *self, PyObject *args, PyObject *keywds)
{
    if (self->LCP==NULL){
        PyErr_SetString(RevealError, "Index not yet constructed, alignment stopped.");
        return NULL;
    }

    PyObject *mumpicker;
    PyObject *graphalign;

    static char *kwlist[] = {"mumpicker","align","threads",NULL};
    int numThreads = 0; /* Number of alignment threads */

    if (!PyArg_ParseTupleAndKeywords(args, keywds, "OO|i", kwlist, &mumpicker, &graphalign, &numThreads))
        return NULL;
    
    int i;
    time_t tstart,tfinish;
    
    index_queue=malloc(QUEUE_BUF*sizeof(RevealIndex *));
    
    pthread_t *tids=malloc(numThreads*sizeof(pthread_t));
    pthread_attr_t attr; 
    
    pthread_mutex_init(&mutex, NULL);
    pthread_mutex_init(&python, NULL);
    pthread_attr_init(&attr);
    
    time(&tstart);
    
    self->depth=0;
    self->main=(PyObject*)self;
    index_queue[0]=self;
    
    qsize=1;
    qstart=0;
    aw=0;
    nmums=0;
    
    Py_INCREF(self); //make sure main index isn't gc'ed during alignment 
    
    if (numThreads>0){
        
        Py_BEGIN_ALLOW_THREADS;
        
        for(i = 0; i < numThreads; i++) {
            fprintf(stderr,"Creating thread %d\n",i);
            RevealWorker *rw;
            rw=malloc(sizeof(RevealWorker));
            rw->threadid=i;
            rw->mumpicker=mumpicker;
            rw->graphalign=graphalign;
            int rv;
            rv=pthread_create(&tids[i],&attr,aligner,rw);
            if (rv!=0){
                PyErr_SetString(RevealError, "Failed to start alignment thread.");
                return NULL;
            }
        }
        
        while (1) {
            if (aw==0 && qsize==qstart){
                break; //successfully aligned quit
            }
             
            if (err_flag){
                fprintf(stderr,"Error occurred in one the the alignment threads.\n");
                //TODO: iterate over remaining indices in the queue and free them
                break; //an error occurred in one of the threads
            }
            usleep(1);
        }
        
        //fprintf(stderr,"Alignment done, terminating threads...\n");

        die=1; //signal workers to terminate
        
        //join worker threads
        for(i = 0; i < numThreads; i++) {
            pthread_join(tids[i], NULL);
        }
        
        free(tids);
        
        Py_END_ALLOW_THREADS

    } else {
        //dont use threads, just use main thread
        RevealWorker *rw;
        rw=malloc(sizeof(RevealWorker));
        rw->threadid=-1;
        rw->mumpicker=mumpicker;
        rw->graphalign=graphalign;
        aligner(rw);
    }
    
    time(&tfinish);
    //fprintf(stderr,"Alignment based on %d MUMs, produced in %.f seconds.\n",nmums,difftime(tfinish,tstart));
    
    free(index_queue);
    
    pthread_mutex_destroy(&mutex);
    pthread_mutex_destroy(&python);

    if (err_flag==0){
        Py_INCREF(Py_None);
        return Py_None;
    } else {
        return NULL;
    }
};

static PyObject *
reveal_getbestmultimum(RevealIndex *self, PyObject *args, PyObject *keywds)
{
    fprintf(stderr,"in reveal_getbestmultimum\n");
    RevealMultiMUM mmum;
    mmum.sp=(int *) malloc(self->nsamples*sizeof(int));
    mmum.l=0;
    int min_n=2,i=0;
    
    //getbestmultimum(RevealIndex *index, RevealMultiMUM *mum, int min_n)
    if (getbestmultimum(self, &mmum, min_n)==0) {
        fprintf(stderr,"Found best multimum\n");
        PyObject *sps;
        PyObject *arglist;
        sps=PyList_New(mmum.n);
        PyObject *pos;
        for (i=0;i<mmum.n;i++){
            pos=Py_BuildValue("i",mmum.sp[i]);
            PyList_SetItem(sps,i,pos);
        }
        arglist = Py_BuildValue("(i,i,O)", mmum.l, mmum.n, sps);
        return arglist;
    } else {
        return NULL;
    }
}

static PyMethodDef reveal_methods[] = {
    { "align", (PyCFunction) align, METH_VARARGS|METH_KEYWORDS },
    { "addsample", (PyCFunction) addsample, METH_VARARGS },
    { "addsequence", (PyCFunction) addsequence, METH_VARARGS },
    { "construct", (PyCFunction) construct, METH_VARARGS },
    { "getbestmultimum", (PyCFunction) reveal_getbestmultimum, METH_VARARGS },
    { "getmultimems", (PyCFunction) getmultimums, METH_VARARGS },
    { "getmums", (PyCFunction) getmums, METH_VARARGS },
    { NULL, NULL }
};

static int
reveal_init(RevealIndex *self, PyObject *args, PyObject *kwds)
{
    if (args==NULL) {
        return 0;
    }
    self->T=NULL;
    self->SA=NULL;
    self->LCP=NULL;
    self->SAi=NULL;
    self->SO=NULL;
    self->nsep=NULL;
    self->depth=0;
    self->n=0;
    self->nsamples=0;
    self->samples = PyList_New(0);
    self->nodes = PyList_New(0);
    Py_INCREF(Py_None);
    self->left=Py_None;
    Py_INCREF(Py_None);
    self->right=Py_None;
    return 0;
}

static PyObject *
reveal_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    RevealIndex *self;

    self = (RevealIndex *)type->tp_alloc(type, 0);

    if (self!=NULL) {
        //fprintf(stderr,"New index!\n");
        //pre-init here...
    }
    
    return (PyObject *)self;
}

static PyObject *
reveal_getT(RevealIndex *self, void *closure)
{
    //keep track of whether T is not freed yet!
    return Py_BuildValue("s",self->T);
}

static PyObject *
reveal_getSA(RevealIndex *self, void *closure)
{
    if (self->SA==NULL) {
        PyErr_SetString(PyExc_TypeError, "Index not yet constructed.");
        return NULL;
    }

    PyObject *lst = PyList_New(self->n);

    if (!lst)
        return NULL;

    int i;
    for (i = 0; i < self->n; i++) {
        PyObject *num = Py_BuildValue("i", self->SA[i]);
        if (!num) {
            Py_DECREF(lst);
            return NULL;
        }
        PyList_SET_ITEM(lst, i, num);
    }
    return lst;
}

static PyObject *
reveal_getSO(RevealIndex *self, void *closure)
{
    if (self->SO==NULL){// || self->nsamples<3) {
        PyErr_SetString(PyExc_TypeError, "SO not available.");
        return NULL;
    }

    PyObject *lst = PyList_New(self->n);

    if (!lst)
        return NULL;

    int i;
    for (i = 0; i < self->n; i++) {
        PyObject *num = Py_BuildValue("i", self->SO[i]);
        if (!num) {
            Py_DECREF(lst);
            return NULL;
        }
        PyList_SET_ITEM(lst, i, num);
    }
    return lst;
}

static PyObject *
reveal_getLCP(RevealIndex *self, void *closure)
{
    if (self->LCP==NULL) {
        PyErr_SetString(PyExc_TypeError, "Index not yet constructed.");
        return NULL;
    }

    PyObject *lst = PyList_New(self->n);

    if (!lst)
        return NULL;

    int i;
    for (i = 0; i < self->n; i++) {
        PyObject *num = Py_BuildValue("i", self->LCP[i]);
        if (!num) {
            Py_DECREF(lst);
            return NULL;
        }
        PyList_SET_ITEM(lst, i, num);
    }
    return lst;
}

static PyObject *
reveal_getSAi(RevealIndex *self, void *closure)
{
    if (self->SAi==NULL) {
        PyErr_SetString(PyExc_TypeError, "Index not yet constructed.");
        return NULL;
    }

    PyObject *lst = PyList_New(self->n);

    if (!lst)
        return NULL;

    int i;
    for (i = 0; i < self->n; i++) {
        PyObject *num = Py_BuildValue("i", self->SAi[i]);
        if (!num) {
            Py_DECREF(lst);
            return NULL;
        }
        PyList_SET_ITEM(lst, i, num);
    }
    return lst;
}

static PyObject *
reveal_getnsep(RevealIndex *self, void *closure)
{
    PyObject *lst = PyList_New(self->nsamples-1);

    if (!lst)
        return NULL;

    int i;
    for (i = 0; i < (self->nsamples-1); i++) {
        PyObject *num = Py_BuildValue("i", self->nsep[i]);
        if (!num) {
            Py_DECREF(lst);
            return NULL;
        }
        PyList_SET_ITEM(lst, i, num);
    }
    return lst;
}

static PyObject *
reveal_getn(RevealIndex *self, void *closure)
{
    return Py_BuildValue("i",self->n);
}

static PyObject *
reveal_getnsamples(RevealIndex *self, void *closure)
{
    return Py_BuildValue("i",self->nsamples);
}

static PyObject *
reveal_getsamples(RevealIndex *self, void *closure)
{
    return self->samples;
}

static PyObject *
reveal_getnodes(RevealIndex *self, void *closure)
{
    Py_INCREF(self->nodes);
    return self->nodes;
}

static PyObject *
reveal_left(RevealIndex *self, void *closure)
{
    Py_INCREF(self->left);
    return self->left;
}

static PyObject *
reveal_right(RevealIndex *self, void *closure)
{
    Py_INCREF(self->right);
    return self->right;
}

static PyGetSetDef reveal_getseters[] = {
    {"n",
        (getter)reveal_getn, NULL,
        "Number of characters in the index.",
        NULL},
    {"nsamples",
        (getter)reveal_getnsamples, NULL,
        "Number of samples in the index.",
        NULL},
    {"samples",
        (getter)reveal_getsamples, NULL,
        "Returns a list of sample/file names that are used in the index.",
        NULL},
    {"nodes",
        (getter)reveal_getnodes, NULL,
        "Returns the set of intervals or nodes associated with the index.",
        NULL},
    {"left",
        (getter)reveal_left, NULL,
        "Returns the interval of the node bounding the index on the left.",
        NULL},
    {"right",
        (getter)reveal_right, NULL,
        "Returns the interval of the node bounding the index on the right.",
        NULL},
    {"nsep",
        (getter)reveal_getnsep, NULL,
        "Returns the number of indices of the sentinels that seperate the various samples in the index.",
        NULL},   
    {"SA",
        (getter)reveal_getSA, NULL,
        "The suffix array of the concatenation of input texts.",
        NULL},
    {"SAi",
        (getter)reveal_getSAi, NULL,
        "The inverse of the suffix array.",
        NULL},
    {"SO",
        (getter)reveal_getSO, NULL,
        "The array with sample id's for every suffix (in case of n>2).",
        NULL},
    {"LCP",
        (getter)reveal_getLCP, NULL,
        "List specifying the length of the common prefix of consecutive values in the LCP array.",
        NULL},
    {"T",
        (getter)reveal_getT, NULL,
        "The concatenation of the input texts.",
        NULL},
    {NULL}  /* Sentinel */
};

static void
reveal_dealloc(RevealIndex *self)
{
    //fprintf(stderr,"dealloc index %d!\n",self->depth);
    if (self->depth==0){ //only there for the main index
        //fprintf(stderr,"dealloc MAIN index!\n");
        if (self->T!=NULL){
            free(self->T);
        }
        if (self->SAi!=NULL){
            free(self->SAi); //doesnt have to be there! fails when never constructed!
        }
        if (self->SO!=NULL){
            free(self->SO);
        }
        if (self->nsep!=NULL){
            free(self->nsep);
        }
        if (self->SA!=NULL){
            free(self->SA); //Should only be free'd here when no alignment was constructed!
        }
        if (self->LCP!=NULL){
            free(self->LCP); //Should only be free'd here when no alignment was constructed!
        }
        
        Py_DECREF(self->nodes);
        Py_DECREF(self->samples);
        Py_DECREF(self->left);
        Py_DECREF(self->right);
    } else {
        //fprintf(stderr,"dealloc SUB index!\n");
        //Py_DECREF(self->main);
        Py_DECREF(self->nodes);
        Py_DECREF(self->samples);
        Py_DECREF(self->left);
        Py_DECREF(self->right);
        if (self->SA!=NULL){
            free(self->SA);
        }
        if (self->LCP!=NULL){
            free(self->LCP);
        }
    }
}

static PyTypeObject RevealIndexType = {
    PyObject_HEAD_INIT(NULL)
    0,                         /*ob_size*/
    "reveal",            /*tp_name*/
    sizeof(RevealIndex),       /*tp_basicsize*/
    0,                         /*tp_itemsize*/
    (destructor)reveal_dealloc, /*tp_dealloc*/
    0,                         /*tp_print*/
    0,                         /*tp_getattr*/
    0,                         /*tp_setattr*/
    0,                         /*tp_compare*/
    0,                         /*tp_repr*/
    0,                         /*tp_as_number*/
    0,                         /*tp_as_sequence*/
    0,                         /*tp_as_mapping*/
    0,                         /*tp_hash */
    0,                         /*tp_call*/
    0,                         /*tp_str*/
    0,                         /*tp_getattro*/
    0,                         /*tp_setattro*/
    0,                         /*tp_as_buffer*/
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE, /*tp_flags*/
    "Reveal Index",            /* tp_doc */
    0,                         /* tp_traverse */
    0,                         /* tp_clear */
    0,                         /* tp_richcompare */
    0,                         /* tp_weaklistoffset */
    0,                         /* tp_iter */
    0,                         /* tp_iternext */
    reveal_methods,            /* tp_methods */
    0,                         /* tp_members */
    reveal_getseters,          /* tp_getset */
    0,                         /* tp_base */
    0,                         /* tp_dict */
    0,                         /* tp_descr_get */
    0,                         /* tp_descr_set */
    0,                         /* tp_dictoffset */
    (initproc)reveal_init,     /* tp_init */
    0,                         /* tp_alloc */
    reveal_new,                         /* tp_new */
};

RevealIndex* newIndex()
{
    return (RevealIndex *) PyObject_CallObject((PyObject *) &RevealIndexType, NULL);
}

#ifndef PyMODINIT_FUNC  /* declarations for DLL import/export */
#define PyMODINIT_FUNC void
#endif
PyMODINIT_FUNC
initreveallib(void)
{
    PyObject* m;
    
    if (PyType_Ready(&RevealIndexType) < 0)
        return;
    
    m = Py_InitModule3("reveallib", NULL, "REcursiVe Exact matching ALigner");
    
    Py_Initialize();
    PyEval_InitThreads();

    Py_INCREF(&RevealIndexType);
    PyModule_AddObject(m, "index", (PyObject *)&RevealIndexType);
    
    RevealError = PyErr_NewException("Reveal.error", NULL, NULL);
    Py_INCREF(RevealError);
    PyModule_AddObject(m, "error", RevealError);
}

/*PyMODINIT_FUNC
initreveal64(void)
{
    initreveal();
}*/
